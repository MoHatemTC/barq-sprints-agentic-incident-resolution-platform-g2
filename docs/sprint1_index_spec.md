# Sprint 1 — Vector Index Specification (S1.4)

**Owner:** Abdullah Ashraf
**Status:** Built and verified against the working corpus (`docs/sprint1_corpus_design.md`)

This document describes what was actually implemented, not what was
originally planned — see §7 for deviations from the initial design.

## 1. Infrastructure

Qdrant runs via the shared `docker-compose.yml` at the repo root, alongside
PostgreSQL and Redis (scaffolded for later sprints, not yet used by S1.4).

| Service | Image | Port | Persistence |
|---|---|---|---|
| qdrant | `qdrant/qdrant:v1.12.4` (pinned) | `6333` (HTTP), `6334` (gRPC) | named volume `qdrant_data` |
| postgres | `postgres:16` | `5432` | named volume `pg_data` |
| redis | `redis:7-alpine` | `6379` | none (not needed yet) |

All three services include health checks; `docker compose ps` reports
`healthy` within ~10–15 seconds of `docker compose up -d`.

## 2. Collection

| Property | Value |
|---|---|
| Name | `barq_knowledge_base` (configurable via `QDRANT_COLLECTION_NAME` env var) |
| Dense vector | size `384`, distance `Cosine` |
| Sparse vector | named `sparse`, no fixed size (Qdrant sparse vectors are variable-length by design) |

Both vector types are stored on every point, enabling hybrid (dense + sparse)
retrieval once query-time fusion logic is built in Sprint 2/3. Query-time
search implemented so far is dense-only (see §8b).

## 3. Embedding models

| Type | Model | Dimensionality |
|---|---|---|
| Dense | `BAAI/bge-small-en-v1.5` (via `sentence-transformers`) | 384 |
| Sparse | `Qdrant/bm25` (via `fastembed`) | variable (BM25-style term weights) |

Model names are configurable via `.env` (`DENSE_EMBEDDING_MODEL`,
`SPARSE_EMBEDDING_MODEL`), defaulting to the values above.

### Model-mismatch guard

A marker point (fixed UUID `00000000-0000-0000-0000-000000000001`) is
written to the collection on creation, storing a fingerprint string
(`"{dense_model}::{sparse_model}"`). On every subsequent ingestion run,
this fingerprint is checked against the current configured models before
any upsert happens. If they don't match, ingestion aborts with a
`RuntimeError` rather than silently mixing vectors from two different
embedding spaces into one collection — verified by design, not yet
exercised with an actual mismatch (would require deliberately switching
`DENSE_EMBEDDING_MODEL` and re-running to trigger it).

## 4. Chunking

Implemented in `src/retrieval/chunking.py`. Articles are split on `##`
Markdown section headers; each section becomes exactly one chunk, so a
numbered procedure is never split mid-step. Returns `(section_label,
chunk_text)` pairs — the section label is preserved in the payload,
enabling retrieval results to be attributed to a specific article section
(e.g. "Resolution" vs. "Symptom").

## 5. Point ID scheme

Point IDs are deterministic:

```
raw = f"{article_id}:{version}:{chunk_index}"
point_id = sha256(raw).hexdigest()  # formatted as a UUID string
```

**Why version is included:** an earlier version of this scheme used only
`article_id:chunk_index`, which caused KB0010 v1 and v2 (the real
near-duplicate pair) to collide on the same point IDs — the retired
version silently overwrote the published one on ingestion. Including
`version` in the hash means both versions of an article coexist as
independent, queryable points, while re-running ingestion on *unchanged*
content still produces the same IDs (idempotent).

## 6. Payload schema

Every point carries:

| Field | Source | Notes |
|---|---|---|
| `sys_id` | article source | Placeholder until S1.5 hand-off supplies real ServiceNow `sys_id` |
| `number` | article source | ServiceNow-style KB number, e.g. `KB0001` |
| `article_id` | article source | Same as `number` for now; used internally for point-ID derivation |
| `title` | article source | |
| `section` | chunking | e.g. `"Resolution"`, `"Symptom"` |
| `category` | article source | see allowed values in corpus design doc §1 |
| `service` | article source | see allowed values in corpus design doc §1 |
| `workflow_state` | article source | `draft` / `published` / `retired` |
| `version` | article source | integer |
| `security_level` | article source | `internal` / `confidential` (inferred for this corpus — see corpus design doc §7) |
| `chunk_index` | chunking | integer position within the article |
| `text` | chunking | the actual chunk content, embedded |

## 7. Deviations from the original design

| Original plan | What was actually built | Why |
|---|---|---|
| Point ID = `article_id:chunk_index` | Point ID = `article_id:version:chunk_index` | Discovered during testing that near-duplicate version pairs collided and silently overwrote each other; fixed before committing |
| Default collection name `kb_articles` | `barq_knowledge_base` | Renamed per team/mentor guidance for clarity |
| Article source: local `.md` files with YAML frontmatter | Pluggable source abstraction (`src/retrieval/sources/`) reading from JSON, with a stub for a future ServiceNow-backed source | Needed to match the confirmed Path B JSON schema (`article_number`, `body`, etc.) and to support a clean swap to ServiceNow's Table API in a later sprint without rewriting the ingestion logic |
| Payload: `article_id`, `title`, `category`, `service`, `workflow_state`, `version`, `security_level`, `chunk_index`, `text` | Same, plus `sys_id`, `number`, `section` | Added per mentor feedback (Sarah) specifying the exact payload fields expected |
| No shared dedupe logic | `src/retrieval/article_utils.py::dedupe_articles()`, used by both `publish_kb.py` and `local_json_source.py` | Originally wrote dedupe logic separately in the publishing script; recognized the vector store needed the same filtering (keep highest-version, non-retired record per article) and extracted it into one shared function instead of duplicating |

## 8. Verification results

Verified against the corpus as it evolved during development:

| Test | Result |
|---|---|
| Clean ingestion from empty collection (initial 27-record corpus, before dedupe) | ✅ 84 points, 384-dim dense vectors |
| Idempotent re-ingestion (same content, run twice) | ✅ Point count unchanged run-over-run |
| Persistence across full container restart (`docker compose down && up`) | ✅ Point count unchanged, no re-ingestion required to observe this — data was retained on the named volume |
| Clean ingestion after `dedupe_articles()` wired in (24 published, non-retired articles) | ✅ 76 points, 384-dim dense vectors |

Point count dropped from 84 → 76 after dedupe was applied, consistent with
removing the two retired-only articles (KB0012, KB0022) that have no
published counterpart and should never have been indexed.

## 8b. Retrieval smoke test (dense search)

`src/retrieval/smoke_test.py` runs a small set of real queries against the
index to prove retrieval actually works, not just that ingestion succeeded.
Search is dense-only at this stage; hybrid dense+sparse fusion is Sprint
2/3 scope.

| Query | Expected article | Result |
|---|---|---|
| "VPN authentication keeps failing after I changed my password" | KB0001 | ✅ top result, score 0.822 |
| "Outlook shows disconnected and no email is coming through" | KB0002 | ✅ top result, score 0.772 |
| "My account got locked after too many failed login attempts" | KB0005 | ✅ top result, score 0.751 |
| "requesting annual leave for next month" (deliberately unanswerable) | none | top match KB0020, score 0.646 |

Correct matches scored 0.72–0.82; the best-matching irrelevant result for a
genuinely out-of-scope query scored 0.646 — a real, observed gap of
roughly 0.07–0.17. This is evidence (not a guess) for where Sprint 3's
confidence threshold should sit, since dense similarity alone cannot
self-reject an out-of-scope query — it will always return whatever is
topically closest, relevant or not.

This test also caught a real bug during development: after
`dedupe_articles()` was added, two retired articles (KB0012, KB0022) were
still returned by search because ingestion only upserts — it never removes
points for articles no longer in the corpus. Deleting and re-ingesting the
collection resolved it for this sprint (see §9 for the underlying
limitation, not yet fixed at the pipeline level).

## 9. Known limitations / not yet implemented

- No query-time **hybrid** (dense+sparse fusion) retrieval yet — only dense
  search has been implemented and verified (§8b). Sparse vectors are
  stored on every point but not yet queried. Hybrid search with reranking
  is Sprint 2/3 scope.
- **Ingestion is upsert-only**; it does not remove points for articles that
  have been deleted or excluded from the source corpus (e.g. by dedupe
  logic added after initial ingestion). This was caught directly by the
  retrieval smoke test (§8b) — two retired articles remained searchable
  after a corpus/logic change until the collection was deleted and
  recreated. Currently mitigated manually; a proper fix would diff the
  corpus against existing point IDs and delete orphans automatically.
- `sys_id` is currently a placeholder (mirrors `article_number`) since
  articles are read from local JSON, not yet from ServiceNow's Table API.
  This will be corrected once S1.5's read client is wired into
  `src/retrieval/sources/servicenow_source.py`.
- Confidence threshold value is not yet decided or implemented — §8b's
  results are the evidence for that decision, not the decision itself.
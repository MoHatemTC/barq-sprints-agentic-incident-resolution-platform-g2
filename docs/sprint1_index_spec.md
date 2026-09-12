# Sprint 1 — Vector Index Specification (S1.4)

**Owner:** Abdullah Ashraf
**Status:** Built and verified against the working corpus (`docs/sprint1_corpus_design.md`)

Describes what was actually implemented, not what was originally planned — see §7 for deviations.

## 1. Infrastructure

Qdrant runs via the shared `docker-compose.yml`, alongside PostgreSQL and Redis (scaffolded for later sprints, not yet used by S1.4).

| Service | Image | Port | Persistence |
|---|---|---|---|
| qdrant | `qdrant/qdrant:v1.12.4` (pinned) | 6333 (HTTP), 6334 (gRPC) | named volume `qdrant_data` |
| postgres | `postgres:16` | 5432 | named volume `pg_data` |
| redis | `redis:7-alpine` | 6379 | none (not needed yet) |

All three include health checks; `docker compose ps` reports `healthy` within ~10–15s of `docker compose up -d`.

## 1b. ServiceNow OAuth integration identity

Publishing (`publish_kb.py`) and permission verification (`scripts/verify_permissions.py`) authenticate via OAuth password grant against the S1.2 integration identity.

**Scope Restriction: Broadly scoped — required, not a workaround.**

ServiceNow's Table API (`/api/now/table/...`) is an unscoped platform API. "Securely scoped" causes every Table API call to fail with `403 Access to unscoped api is not allowed`. A REST API Access Policy cannot fix this for OAuth clients — its "Inbound Authentication Profiles" only support Basic Auth, WSSE, API Key, and HMAC, with no OAuth option. Confirmed with the mentor: "Broadly scoped" is the correct setting for OAuth clients calling the Table API; granular OAuth restriction uses a separate mechanism (REST API Auth Scopes), not used here. The real S1.2 identity was updated accordingly.

| Setting | Value | Why |
|---|---|---|
| Scope Restriction | **Broadly scoped** | Required for OAuth clients to call the Table API at all |
| Internal Integration User | *(recommend checking on the real identity)* | Restricts interactive UI login, per FR-06 least-privilege — flagged to the S1.2 owner |

Verified capabilities (`scripts/verify_permissions.py`), re-confirmed after the scope fix:

| Operation | Table | Result |
|---|---|---|
| Authenticate | — | ✅ |
| Read | `incident` | ✅ 200 |
| Read | `kb_knowledge` | ✅ 200 |
| Write (create + delete) | `kb_knowledge` | ✅ 201 / 204 |

## 2. Collection

| Property | Value |
|---|---|
| Name | `barq_knowledge_base` (via `QDRANT_COLLECTION_NAME`) |
| Dense vector | size 384, distance Cosine |
| Sparse vector | named `sparse`, variable-length |

Both vector types are stored on every point, enabling hybrid retrieval once query-time fusion is built (Sprint 2/3). Search implemented so far is dense-only (§8b).

## 3. Embedding models

| Type | Model | Dim |
|---|---|---|
| Dense | `BAAI/bge-small-en-v1.5` | 384 |
| Sparse | `Qdrant/bm25` | variable |

Configurable via `.env` (`DENSE_EMBEDDING_MODEL`, `SPARSE_EMBEDDING_MODEL`).

**Model-mismatch guard:** a marker point (fixed UUID) stores a fingerprint (`"{dense}::{sparse}"`) on collection creation. Every ingestion run checks the current models against it and aborts with `RuntimeError` on mismatch, rather than silently mixing embedding spaces. Verified by design, not yet triggered against a real mismatch.

## 4. Chunking

`src/retrieval/chunking.py` splits on `##` Markdown headers — each section becomes one chunk, so numbered procedures never split mid-step. Returns `(section_label, chunk_text)` pairs; the label is stored in the payload for result attribution.

## 5. Point ID scheme

```
raw = f"{article_id}:{version}:{chunk_index}"
point_id = sha256(raw).hexdigest()  # as a UUID string
```

**Why version is included:** an earlier scheme (`article_id:chunk_index` only) caused KB0010 v1/v2 to collide — the retired version silently overwrote the published one. Adding `version` lets both coexist as independent points while unchanged content still re-hashes to the same ID (idempotent).

## 6. Payload schema

| Field | Source | Notes |
|---|---|---|
| `sys_id` | source | Placeholder until S1.5 hand-off |
| `number` | source | e.g. `KB0001` |
| `article_id` | source | = `number` for now; used in point-ID derivation |
| `title` | source | |
| `section` | chunking | e.g. `"Resolution"` |
| `category` | source | see corpus design doc §1 |
| `service` | source | see corpus design doc §1 |
| `workflow_state` | source | draft / published / retired |
| `version` | source | integer |
| `security_level` | source | internal / confidential (inferred — corpus design doc §7) |
| `chunk_index` | chunking | position within article |
| `text` | chunking | embedded chunk content |

## 7. Deviations from the original design

| Planned | Built | Why |
|---|---|---|
| Point ID = `article_id:chunk_index` | + `version` | Near-duplicate pairs collided; fixed before committing |
| Collection name `kb_articles` | `barq_knowledge_base` | Mentor guidance |
| `.md` files + YAML frontmatter | Pluggable source abstraction reading JSON, ServiceNow stub | Matches confirmed Path B schema; clean swap to Table API later |
| Base payload fields | + `sys_id`, `number`, `section` | Mentor-specified exact fields |
| No shared dedupe | `article_utils.py::dedupe_articles()`, used by `publish_kb.py` + `local_json_source.py` | Avoid duplicating the same filtering logic in two places |
| Scope Restriction: securely scoped (assumed) | Broadly scoped | Table API is unscoped; confirmed with mentor (§1b) |

## 8. Verification results

| Test | Result |
|---|---|
| Clean ingestion, initial 27-record corpus (pre-dedupe) | ✅ 84 points, 384-dim |
| Idempotent re-ingestion | ✅ count unchanged |
| Persistence across full restart | ✅ count unchanged, no re-ingestion needed |
| Clean ingestion post-dedupe (24 published articles) | ✅ 76 points |

84 → 76 matches removing KB0012 and KB0022 (retired-only, no published counterpart — should never have been indexed).

## 8b. Retrieval smoke test (dense search)

`src/retrieval/smoke_test.py` proves retrieval works, not just that ingestion succeeded. Dense-only; hybrid fusion is Sprint 2/3 scope.

| Query | Expected | Result |
|---|---|---|
| "VPN authentication keeps failing after I changed my password" | KB0001 | ✅ top, 0.822 |
| "Outlook shows disconnected and no email is coming through" | KB0002 | ✅ top, 0.772 |
| "My account got locked after too many failed login attempts" | KB0005 | ✅ top, 0.751 |
| "requesting annual leave for next month" (unanswerable) | none | KB0020, 0.646 |

Correct matches: 0.72–0.82. Best irrelevant match: 0.646 — a real, observed gap of ~0.07–0.17, giving Sprint 3 actual evidence for where the confidence threshold should sit, since dense similarity alone can't self-reject an out-of-scope query.

Also caught a real bug: after adding `dedupe_articles()`, KB0012/KB0022 still returned in search — ingestion only upserts, it never removes points for articles no longer in the corpus. Fixed for this sprint by deleting and re-ingesting the collection (see §9).

## 9. Known limitations

- **No hybrid retrieval yet** — sparse vectors are stored but not queried; dense-only search verified (§8b). Sprint 2/3 scope.
- **Ingestion is upsert-only** — doesn't remove points for articles no longer in the corpus. Caught by the smoke test (§8b); currently mitigated by deleting/recreating the collection. A proper fix would diff the corpus against existing point IDs and delete orphans.
- **`sys_id` is a placeholder** (mirrors `article_number`) until S1.5's read client is wired into `servicenow_source.py`.
- **Confidence threshold** not yet decided — §8b is the evidence, not the decision.
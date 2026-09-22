# Sprint 2 — S2.4 Hybrid Retrieval: Fusion, Metadata Filtering, Reranking & Baseline Measurement

**Author:** Bassant Hossam  , **Branch:** `s2.4-Retrieval-Engineering` 

## Summary

The query side of the retrieval pipeline is implemented in `src/retrieval/` as three modes selected by one
environment variable (`RETRIEVAL_MODE=dense | hybrid | hybrid_rerank`) with no code changes. Metadata
pre-filters on `category`, `service`, `workflow_state`, `version` and `security_level` are passed *into*
every Qdrant search, so excluded chunks are never candidates. A planted-document test (13 cases) and a live
run with three decoys inside the collection (0 hits in 222 searches) prove the filter.

A single command, `python eval/ablation.py`, grades all three modes against a versioned evaluation set
(v1.1: 27 incidents from the S1.4 coverage matrix + 10 identifier probes) and writes the tables below.
Two full runs produce identical rankings.

| k = 5 | precision@5 | recall@5 | MRR | p95 ms |
|---|---|---|---|---|
| dense (baseline) | 0.376 | 0.906 | 0.969 | 112 |
| hybrid | 0.387 (+0.011) | 0.906 (+0.000) | 0.922 (−0.047) | 83 |
| hybrid_rerank | **0.450 (+0.074)** | **0.938 (+0.032)** | 0.948 (−0.021) | 1422 |

**Findings.** Dense is already strong on this corpus (21/22 incidents rank the right article first).
Hybrid's value is recall at depth (k=10: 0.938 → 0.984, hit rate → 1.000) and three concrete recovered
articles that dense never surfaces. The cross-encoder reranker gives the best precision and recall but, on
CPU, is ~3× over the 500 ms latency budget. **Recommendation:** default `hybrid` on the interactive path
(p95 83 ms, 417 ms headroom); enable `hybrid_rerank` where latency is not user-facing or where a GPU is
available. Both are one env change.

---

## 1. Fusion & Reranking Architecture

### 1.1 The three modes and the configuration switch

```
query ──▶ build_qdrant_filter() ──▶ ┌ dense         : 1 filtered search on "dense"            → top_k
                                    ├ hybrid        : 2 filtered searches (dense, sparse)      → RRF → top_k
                                    └ hybrid_rerank : 2 filtered searches → RRF → top 20 → cross-encoder → top_k
```

All settings live in `src/config.py` (`RetrievalConfig`), read from `.env`, validated at import time:

| env key | default | meaning |
|---|---|---|
| `RETRIEVAL_MODE` | `hybrid_rerank` | `dense` / `hybrid` / `hybrid_rerank` — the switch |
| `RETRIEVAL_TOP_K` | 5 | passages passed to generation |
| `RETRIEVAL_CANDIDATE_K` | 20 | candidates per branch before fusion / reranking |
| `RRF_K` | 60 | Reciprocal Rank Fusion constant |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | cross-encoder (fastembed) |
| `ALLOWED_WORKFLOW_STATES` | `published` | pre-filter: states that may be retrieved |
| `BLOCKED_SECURITY_LEVELS` | `restricted` | pre-filter: levels that are never retrieved |
| `LATENCY_BUDGET_MS` | 500 | budget for the headroom table |
| `QDRANT_LOCAL_PATH` | *(empty)* | non-empty → embedded Qdrant instead of Docker |

The collection is S1.4's `barq_knowledge_base` (Qdrant 1.12.4, one collection, two named vectors:
`dense` = `BAAI/bge-small-en-v1.5` 384-d cosine, `sparse` = `Qdrant/bm25`). Sprint 1 stored both vectors;
Sprint 1's smoke test queried only the dense one. No Sprint 1 code was modified.

### 1.2 Metadata pre-filtering (`src/retrieval/filters.py`)

`RetrievalFilters` is a frozen dataclass; `build_qdrant_filter()` turns it into a Qdrant `Filter`:

| clause | condition | source |
|---|---|---|
| `must` | `workflow_state ∈ ALLOWED_WORKFLOW_STATES` | config |
| `must_not` | `security_level ∈ BLOCKED_SECURITY_LEVELS` | config |
| `must_not` | `_is_marker == true` (S1.4's model-fingerprint point) | always |
| `must` (optional) | `category == …`, `service == …`, `version == …` | caller, composable — each set field adds one AND condition |

The filter object is passed as `query_filter=` to **every** `client.query_points()` call — the dense search
and, in hybrid modes, both the dense and the sparse branch — inside `_search_one()`, the only function that
talks to Qdrant. Qdrant applies it *during* the vector search, so an excluded chunk cannot win a similarity
race and be dropped afterwards; it is never a candidate. On the live collection the default filter reduces
90 points to 87 searchable (−1 marker, −2 draft chunks).

### 1.3 Fusion: Reciprocal Rank Fusion (`hybrid_search.rrf_fuse`)

**Score scales.** Dense returns cosine similarity (observed 0.55–0.93); sparse returns BM25 (unbounded,
typically 3–30). They cannot be added. RRF uses only each chunk's *rank* in each list:

```
score(chunk) = Σ over lists  1 / (k + rank_in_list)        k = RRF_K = 60, rank starts at 1
```

A chunk found by both branches gets two terms and rises; a chunk found by one branch still enters the pool.
No normalisation is needed and no floating-point comparison happens between the two scales. k = 60 is the
value from the original RRF paper (Cormack et al. 2009); with 20-candidate lists it keeps the top ranks
distinguishable (1/61 = 0.0164 vs 1/62 = 0.0161) without letting a single rank-1 dominate.

**Worked example (k = 60):**

| chunk | dense rank | sparse rank | RRF score | final |
|---|---|---|---|---|
| A | 1 | 3 | 1/61 + 1/63 = 0.03227 | 1 |
| C | 3 | 1 | 1/63 + 1/61 = 0.03227 | 2 (tie → id) |
| B | 2 | — | 1/62 = 0.01613 | 3 |
| D | — | 2 | 1/62 = 0.01613 | 4 (tie → id) |

**Tie-breaking.** Fused results are sorted by `(−score, point_id)`: highest score first; equal scores resolve
by the lexicographically smaller point id. The same rule is used after reranking. Ties therefore never depend
on dictionary order or on which branch returned first.

**Why RRF is implemented in Python rather than Qdrant's built-in `FusionQuery`.** Qdrant's server-side RRF
hard-codes `k` and does not expose its tie-break. Doing it in 12 lines lets `RRF_K` come from configuration
and makes the tie-break explicit and testable (`tests/test_hybrid_search.py::test_rrf_fuse_*`). Cost: two
round-trips instead of one — ~1 ms on a local collection.

### 1.4 Cross-encoder reranking (`src/retrieval/rerank.py`)

- **Model:** `Xenova/ms-marco-MiniLM-L-6-v2` via `fastembed.rerank.cross_encoder.TextCrossEncoder`
  (ONNX, CPU). Configurable through `RERANK_MODEL`; a BGE reranker is a config change.
- **Input:** the top `RETRIEVAL_CANDIDATE_K` = 20 fused chunks. **Output:** the top `RETRIEVAL_TOP_K` = 5 by
  cross-encoder score, sorted `(−score, point_id)`.
- The bi-encoder embeds query and chunk separately; the cross-encoder reads the pair `(query, chunk text)`
  together and emits one relevance logit. It replaces the RRF score entirely. It is loaded lazily on first
  use, so `dense` and `hybrid` never pay its load time.
- The reranker can only reorder what the filter let through — it never sees excluded chunks.

### 1.5 Determinism choices

| choice | where | effect |
|---|---|---|
| `SearchParams(exact=True)` | `hybrid_search._search_params` (server only; local mode is always exact) | brute-force instead of HNSW approximation; 93 points → negligible cost; no near-tie reordering between runs |
| RRF on ranks | `rrf_fuse` | no cross-scale arithmetic |
| `(−score, point_id)` tie-break | `rrf_fuse`, `rerank` | equal scores always resolve identically |
| model fingerprint marker | S1.4 `ingest.py` | index and query must use the same embedding models |
| frozen evaluation set | `eval/evaluation_set.json` v1.1 | same exam every run |

---

## 2. Planted Document Filter Proof

### 2.1 Why decoys are needed

S1.4's ingest (`article_utils.dedupe_articles`) drops retired articles before indexing, so the live
collection contains no retired chunk. A filter test against real data would pass for the wrong reason. The
proof therefore uses three **planted** decoys (`eval/planted_documents.json`), each a near-copy of a real
published article so that *without* the filter it is the most similar chunk to its query:

| id | copies | `workflow_state` | `security_level` | excluded by |
|---|---|---|---|---|
| KB9001 | KB0001 VPN (Symptom text, `ERR_VPN_AUTH_004`) | **retired** | internal | allowed-states rule |
| KB9002 | KB0005 lockout (`AADSTS50053`) | **draft** | internal | allowed-states rule |
| KB9003 | KB0022 SAP (`APPSRV-OLD-04`) | published | **restricted** | blocked-security rule **only** |

Each decoy carries a deliberately wrong Resolution ("reboot twice", "unlock without verification") so any
leak would be visible. `eval/planting.py` builds them as Qdrant points with exactly the payload schema of
`ingest.py`, so they pass through the same filter as real chunks.

### 2.2 In-memory test — `tests/test_planted_exclusion.py`

Setup: `QdrantClient(":memory:")`, same schema as production, 3 real published chunks + the 3 decoys.
Queries use `top_k=50` ("return everything").

| test | cases | asserts |
|---|---|---|
| `test_planted_document_is_never_returned` | 3 decoys × 3 modes = 9 | probe each decoy with its own text, default filter → no `KB900x` in the results |
| `test_planted_document_is_rank_1_when_filter_is_off` | 3 | same probe with all states allowed and nothing blocked → the decoy **is rank 1** (proves it really is the most similar chunk, so the first test has teeth) |
| `test_restricted_decoy_is_blocked_by_security_rule_alone` | 1 | KB9003 with only the state rule → returned; with the default filter → not returned |

```
$ pytest tests/test_planted_exclusion.py -p
===================== 13 passed in 33.03s =====================
```

### 2.3 Live collection

`python eval/ablation.py --plant` upserts the same three decoys into Docker's `barq_knowledge_base`
(90 → 93 points; deterministic ids, removable with `--unplant`). Every results file in `eval/results/`
reports **`planted hits = 0`** for every mode: 37 queries × 3 modes × 2 runs = **222 searches, no leak**.

---

## 3. Three-Way Ablation Table & Sparse Wins

### 3.1 Evaluation set — `eval/evaluation_set.json` v1.1

Built by `python eval/build_evaluation_set.py` and committed (versioned; rebuild is a no-op diff).

| source | items | description | ground truth |
|---|---|---|---|
| `coverage_matrix` | 27 | S1.4's `data/coverage_matrix.csv`: real incident summaries — 22 answerable (5 need two articles), 5 intentionally unanswerable | `resolving_article_ids` |
| `identifier_probe` | 10 | identifier-only queries (`ERR_VPN_AUTH_004`, `AADSTS50053`, `0x8004010F`, …), the way a technician pastes an error code into a search box | every article whose body contains the token |

The 10 probe tokens were extracted mechanically from the published article bodies (regex for
upper-case codes, `0x…` values and hostnames), fixed **before** any ablation run, and none were removed
afterwards. Three tokens occur in two articles (`0x8004010F`, `DISP_NOSIGNAL_04`, `RFC_ERROR_COMMUNICATION`);
their ground truth lists both. Results are reported per source (§3.3) so the two slices never blend.

### 3.2 Metrics (article level, answerable items only)

Retrieved chunks are mapped to KB numbers and de-duplicated in rank order. For expected set *E* and returned
set *R*: **context precision@k** = |E ∩ R| / |R|; **context recall@k** = |E ∩ R| / |E|; **hit@k** = |E ∩ R| > 0;
**MRR** = mean of 1 / rank of the first expected article. Unanswerable items report only the top score.
Precision is bounded by k: a single-answer incident with 4 unique articles returned scores at most 0.25, so
precision is meaningful *relative between modes*, not against 1.0.

### 3.3 Three-way comparison

**k = 5, candidate pool 20** (`eval/results/ablation_k5.md`, 2 runs, decoys planted):

| mode | precision@5 | recall@5 | hit@5 | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.376 | 0.906 | 0.969 | 0.969 | 0 | 55.6 | 112.0 |
| hybrid | 0.387 | 0.906 | 0.969 | 0.922 | 0 | 68.7 | 82.9 |
| hybrid_rerank | **0.450** | **0.938** | 0.969 | 0.948 | 0 | 922.4 | 1422.4 |

**k = 10** (`ablation_k10.md`):

| mode | precision@10 | recall@10 | hit@10 | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.200 | 0.938 | 0.969 | 0.969 | 0 | 54.7 | 67.8 |
| hybrid | 0.192 | **0.984** | **1.000** | 0.926 | 0 | 94.3 | 159.8 |
| hybrid_rerank | 0.203 | **1.000** | **1.000** | 0.953 | 0 | 1106.3 | 1572.5 |

**By source, k = 5:**

| source | mode | precision | recall | hit | MRR |
|---|---|---|---|---|---|
| coverage_matrix (22) | dense | 0.398 | 0.886 | 0.955 | 0.955 |
| coverage_matrix | hybrid | 0.392 | 0.886 | 0.955 | 0.886 |
| coverage_matrix | hybrid_rerank | 0.447 | 0.909 | 0.955 | 0.924 |
| identifier_probe (10) | dense | 0.328 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid | 0.377 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid_rerank | 0.457 | 1.000 | 1.000 | 1.000 |

### 3.4 Numerical margin over the dense baseline

| mode | k | Δ precision | Δ recall | Δ hit | Δ MRR |
|---|---|---|---|---|---|
| hybrid | 5 | +0.011 | +0.000 | +0.000 | −0.047 |
| hybrid | 10 | −0.008 | **+0.046** | **+0.031** | −0.043 |
| hybrid_rerank | 5 | **+0.074** | **+0.032** | +0.000 | −0.021 |
| hybrid_rerank | 10 | +0.003 | **+0.062** | **+0.031** | −0.016 |

Reading: **hybrid** buys recall and hit rate at depth (reaching 1.000 at k=10) and pays with MRR — BM25
"false friends" on shared tokens pushed the first right article from rank 1 to 2 on three incidents (e.g.
INC1009, where `RFC_ERROR_COMMUNICATION` occurs in both KB0008 and KB0022). **hybrid_rerank** keeps the
recall gain, wins the largest precision gain (+0.074 at k=5) and recovers most of the MRR: it promotes the
chunk that *answers* (Cause / Resolution) over the one that *restates* the question (Symptom).

### 3.5 Concrete sparse wins

A *dense failure* is an expected article absent from dense's top-k; a *sparse win* is hybrid retrieving it.
These rows are produced by `sparse_wins()` in the harness, not selected by hand.

| query | expected | dense top-k | hybrid top-k | recovered | run |
|---|---|---|---|---|---|
| **INC0010064** — "No email, shared drive gone, and Teams repeatedly asking to sign in — three symptoms one morning" | KB0005 | 9 articles, **no KB0005** | KB0005 at rank 7 | **KB0005** | k=10 |
| **PROBE-08** — `0x8004010F` | KB0002, KB0025 | KB0002 only | KB0002, KB0025 | **KB0025** | k=10; k=5 with candidate pool 10 |
| **INC1025** — "Employee requests VPN access and email account setup on first day" | KB0001, KB0018 | KB0001 only | KB0018, KB0001 | **KB0018** | k=5 with candidate pool 10 |

*Why sparse wins here.* `0x8004010F` is an exact token in two articles; cosine similarity of a bare hex code
against a 500-character chunk is weak, but BM25 matches it exactly in both, so the second article enters the
pool. INC0010064 describes three symptoms of one cause (account lockout); no dense neighbour is close, but
the sparse branch matches lockout vocabulary in KB0005 and RRF brings it into the top 10. INC1025 pairs two
unrelated requests; sparse matches "account setup" in KB0018, which dense ranked below five VPN chunks.

### 3.6 What the numbers do not say

- **The corpus is easy for dense.** 25 short KB articles whose titles mirror incident wording; 21/22
  coverage-matrix incidents rank the right article first in every mode. Hybrid's margin at k=5 is small because
  there was little left to gain. On a longer, less title-aligned corpus the gap should widen.
- **Identifier probes did not separate dense from hybrid at rank 1** (both MRR 1.000): bge-small tokenises
  codes into sub-words and still matches. They separated on the *second* article (probe recall 0.950 → 1.000)
  and on precision (0.328 → 0.377 → 0.457).
- **One incident is missed by every mode at k=5**: INC0010064 needs symptom-to-cause reasoning, not
  similarity; hybrid at least surfaces it at k=10.
- **A side finding on confidence.** Cross-encoder top scores for the 5 unanswerable queries are −2.8 … −11.2;
  for answerable queries −6.5 … +8.1. Dense cosine gives 0.63–0.78 vs 0.65–0.93 — no separation. The reranker
  score is a candidate refusal threshold for Sprint 3, with overlap that must be measured, not assumed.

---

## 4. Latency & Reproducibility

### 4.1 Latency headroom

Wall time of `search()` including query embedding; models warm (first call excluded); exact search; 37
queries per mode. Hardware: Intel Core i5-1335U (13th gen, 10 cores), 8 GB RAM, **CPU only**, Windows 11,
Qdrant 1.12.4 in Docker Desktop. Budget: `LATENCY_BUDGET_MS = 500`.

| mode | candidate pool | p50 ms | p95 ms | headroom vs 500 ms | within budget |
|---|---|---|---|---|---|
| dense | — | 55.6 | 112.0 | +388 | yes |
| hybrid | 20 | 68.7 | 82.9 | +417 | yes |
| hybrid_rerank | 20 | 922.4 | 1422.4 | −922 | **no** |
| hybrid_rerank | 10 (`RETRIEVAL_CANDIDATE_K=10`) | 664.3 | 1167.5 | −668 | **no** |

Across all runs (k=5, k=10, two passes) dense p95 stayed in 68–207 ms and hybrid in 83–160 ms; both remain
under budget with ≥ 290 ms headroom. The cross-encoder runs one forward pass per candidate on CPU; it is the
only stage whose cost grows with the candidate pool. Halving the pool cut p50 by ~28 % and cost 0.023
precision (0.450 → 0.427) with recall unchanged (0.938).

**Recommendation.** Default `RETRIEVAL_MODE=hybrid` for the interactive incident path (best recall at
depth, p95 83 ms). Use `hybrid_rerank` for non-user-facing paths (batch enrichment, offline suggestion
generation) or once a GPU is available; the same config knob switches it on. Sprint 3 can also test a
smaller pool (`RETRIEVAL_CANDIDATE_K=8`) or a quantised reranker if rerank latency must be brought under budget.

### 4.2 Determinism evidence

`python eval/ablation.py --runs 2` fingerprints every mode's ranking lists across the full evaluation set and
compares runs: **IDENTICAL rankings in all three modes** (`ablation_k5.json: "deterministic": true`).
Metric values are byte-identical between runs; only latency varies (OS scheduling), which is why it is
reported as percentiles. There are no random seeds in the pipeline: embeddings and the cross-encoder are
deterministic inference, search is exact, fusion uses ranks, and ties resolve by point id.

### 4.3 Reproduce the numbers

```bash
# environment
docker compose up -d qdrant                       # Qdrant 1.12.4, port 6333
pip install -r requirements.txt                   # qdrant-client 1.19.0, fastembed 0.8.0,
                                                  # sentence-transformers 6.0.1, torch 2.14.0, Python 3.13
python -m src.retrieval.ingest                    # S1.4 collection: 90 points

# config (.env) — the values used for every table above
RETRIEVAL_MODE=hybrid_rerank   RETRIEVAL_TOP_K=5   RETRIEVAL_CANDIDATE_K=20   RRF_K=60
RERANK_MODEL=Xenova/ms-marco-MiniLM-L-6-v2
ALLOWED_WORKFLOW_STATES=published   BLOCKED_SECURITY_LEVELS=restricted   LATENCY_BUDGET_MS=500
DENSE_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5   SPARSE_EMBEDDING_MODEL=Qdrant/bm25

# evaluation set and runs
python eval/build_evaluation_set.py               # → eval/evaluation_set.json v1.1 (no-op diff if unchanged)
python eval/ablation.py --plant --runs 2          # → eval/results/ablation_k5.{md,json}   (planted, determinism)
python eval/ablation.py --k 10                    # → eval/results/ablation_k10.{md,json}
RETRIEVAL_CANDIDATE_K=10 python eval/ablation.py --k 5   # → rename to ablation_k5_candidates10.{md,json}

# tests
pytest -q                                         # 105 passe (Sprint 1: 62 · S2.4: 43)
```

Results were produced at commit `081c57c` on branch `s2.4-Retrieval-Engineering`. Per-query ranks, returned
articles, top scores and latencies are in the `.json` files next to each table.

---

## Appendix

### A. Deliverables

| path | role |
|---|---|
| `src/retrieval/filters.py` | `RetrievalFilters`, `build_qdrant_filter()` |
| `src/retrieval/hybrid_search.py` | `search()`, `rrf_fuse()`, `RetrievedChunk`, CLI |
| `src/retrieval/rerank.py` | `rerank()` cross-encoder |
| `src/config.py` | `RetrievalConfig` (`RETRIEVAL`) |
| `eval/build_evaluation_set.py` → `eval/evaluation_set.json` | versioned ground truth v1.1 |
| `eval/planted_documents.json`, `eval/planting.py` | decoys and their Qdrant points |
| `eval/ablation.py` → `eval/results/` | single-command harness and committed results |
| `tests/test_filters.py` (5), `test_hybrid_search.py` (15), `test_rerank.py` (5), `test_planted_exclusion.py` (13), `test_ablation.py` (5) | 43 tests |
| `.env.example`, `README.md` | configuration keys and run instructions |

### B. Manual checks

```
python -m src.retrieval.hybrid_search "0x8004010F" --mode dense  --top-k 10   # KB0002 only
python -m src.retrieval.hybrid_search "0x8004010F" --mode hybrid --top-k 10   # KB0002 and KB0025
python -m src.retrieval.hybrid_search "account locked after failed logins" --mode hybrid_rerank --top-k 3
```

### C. Out of scope / next

Track B (mentor's additional evaluation): index the 52-page *BARQ IT Service Desk Manual* into a second
collection and run the 100-turn `eval/barq_rag_eval_dataset.json` through `eval/adapters.score_retrieval`.
Same `search()` code; separate report section when complete.

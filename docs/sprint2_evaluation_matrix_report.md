# Sprint 2 — Retrieval Evaluation Matrix on the BARQ IT Service Desk Manual

**Author:** Bassant Hossam · **Branch:** `s2.4-Retrieval-Engineering` 

## Summary

The three retrieval modes built for S2.4 (`dense`, `hybrid`, `hybrid_rerank`) were evaluated a second time on
a harder corpus: the 52-page *BARQ IT Service Desk Operations Manual* (Edition 4.0), using the 100-turn
`barq_rag_eval_dataset.json` and the mentor-supplied `adapters.py` scorer. No retrieval code changed; the
manual was parsed into 76 sections, indexed into its own Qdrant collection, and the same `search()` was
graded against section-level ground truth.

| k = 5, 100 turns | precision | recall | all expected found | clean | p95 ms |
|---|---|---|---|---|---|
| dense (baseline) | 0.215 | 0.755 | 0.684 | 0.980 | 80 |
| hybrid | 0.224 (+0.009) | **0.809 (+0.054)** | **0.747 (+0.063)** | 0.980 | 114 |
| hybrid_rerank | 0.223 (+0.008) | 0.811 (+0.056) | 0.747 (+0.063) | 0.980 | 1868 |

**What it shows.** On this corpus hybrid retrieval beats dense clearly: +5.4 points recall at k=5, +9.6 at
k=10, and the harness found 11 turns where sparse matching recovered a section dense never returned. The gain
is concentrated where it matters — `hard` turns 0.43 → 0.54 (k=5) and 0.43 → 0.71 (k=10), `multi_hop`
0.31 → 0.54 / 0.46 → 0.77. The cross-encoder reranker adds nothing over hybrid at k=5 and +2.5 points at
k=10, at 16× the latency. The retired article the dataset forbids (`6.13 KB0010 v1`) was never returned in
600 searches. Two runs produced identical rankings.

---

## 1. How the evaluation matrix works

### 1.1 The flow

```
data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf
        │  src/retrieval/manual_parser.py     52 pages → 76 sections with dataset ids
        ▼                                     ("3.4", "Appendix B.1", "Document control", "6.13 KB0010 v1")
   76 ManualSection
        │  src/retrieval/ingest_manual.py     chunk (500/50) → heading prefix → dense + sparse vectors
        ▼
   Qdrant collection `barq_manual`  (199 points; S1.4's barq_knowledge_base untouched)
        │
        │  eval/ablation_manual.py            for each of 100 turns × 3 modes:
        │                                        search(standalone_input) → section ids of top-k chunks
        ▼                                        → adapters.score_retrieval(ids, turn)
   eval/results/manual_ablation_k5.{md,json}, manual_ablation_k10.{md,json}
```

### 1.2 Parsing the manual (`manual_parser.py`)

The dataset cites the manual by its numbered headings, so chunks must carry those ids. The parser:

| step | what | why |
|---|---|---|
| strip running header/footer | drops the three lines repeated on 51 pages (`BARQ Systems – IT Service Operations Manual`, `INTERNAL DOCUMENT`, `Edition 4.0`) and `N of 52` | the dataset forbids `header_footer_noise` (turn S19-T1) |
| read the table of contents (pages 2–4) | `{id: title}` for every heading | the TOC decides what a heading is — a numbered step like `3. Notify the owner` inside a procedure is never mistaken for section 3 |
| split the body at TOC headings | one `ManualSection` per heading; `B.1 …` lines inside an appendix become `Appendix B.1`; longer ids match first (`12.2` before `12`) | |
| tag KB articles (chapter 6) | read `State / Version / Service` from each article's header block → label `6.4 KB0001 v2`; **6.13 is split into `6.13 KB0010 v1` (retired) and `v2` (published)**; 6.3 (the archived scan) → `archived` | the dataset's forbidden id is `6.13 KB0010 v1`; the existing `workflow_state` filter must be able to see it |
| gate | `--check` lists every id the dataset expects or forbids that the parser did not produce | result: `missing ids: none` — all 54 cited ids present |

Output: 76 sections (74 published, 1 retired, 1 archived), 76 k characters. A chunk from `Appendix B.1` also
counts as `Appendix B` for scoring, because the parent heading has no text of its own.

A note on editions: the file is named *Ed5.1* but the document itself is **Edition 4.0** on every page and in
its version history, matching the dataset's stated corpus. No numbering drift was found.

### 1.3 Indexing (`ingest_manual.py`)

Mirror of S1.4's `ingest.py`: each section is chunked with the shared splitter (`CHUNK_SIZE=500`,
`CHUNK_OVERLAP=50`), every chunk is prefixed with its heading (`6.13 KB0010 v2 KB0010 – Order service …`) so
BM25 can match a section by name, and both named vectors are stored. The payload uses the same keys as S1.4
(`workflow_state`, `security_level`, `category`, `service`, `version`, `text`, …) plus `section_id` and
`section_label`. Because the keys are identical, `filters.py` and `hybrid_search.py` work unchanged — the
collection name is the only difference (`MANUAL_COLLECTION_NAME=barq_manual`). 199 points.

### 1.4 Scoring (`ablation_manual.py` + `adapters.py`)

For each turn the retriever is given `standalone_input` (the self-contained form of the question, as the
dataset recommends for retriever-only evaluation) and returns `top_k` chunks. The set of section ids those
chunks satisfy is passed to the mentor's `score_retrieval()`, which computes per turn:

| field | meaning |
|---|---|
| `precision` | expected sections retrieved ÷ sections retrieved |
| `recall` | expected sections retrieved ÷ expected sections (`None` for the 5 turns whose expected set is `—`) |
| `top_k_contains_all` | every expected section is in the top-k |
| `forbidden_retrieved` / `clean` | any `must_not_retrieve` section in the top-k → not clean |

The harness aggregates: mean precision and recall over turns that expect an answer, **all expected found**
rate, **clean rate** over all 100 turns, forbidden-hit count, p50/p95 latency; `adapters.slice_report()`
gives a pass rate per capability tag (`requires`), difficulty and behaviour, where a turn *passes* only if
all expected sections were found **and** nothing forbidden was returned. `--runs 2` fingerprints all
rankings and compares runs.

**Reading the numbers.** Precision is structurally low: a turn expects one or two sections, we return five
chunks that may span four sections, so the ceiling is ~0.25–0.5. Compare modes to each other. Recall and
*all expected found* are the metrics that answer "did the right section reach the model?".

---

## 2. Results

### 2.1 Three-way comparison

**k = 5** (`eval/results/manual_ablation_k5.md`, two runs, identical rankings):

| mode | precision@5 | recall@5 | all expected found | clean rate | forbidden hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.215 | 0.755 | 0.684 | 0.980 | 3 | 58.9 | 79.6 |
| hybrid | 0.224 | **0.809** | **0.747** | 0.980 | 2 | 79.0 | 114.2 |
| hybrid_rerank | 0.223 | **0.811** | **0.747** | 0.980 | 2 | 1234.3 | 1868.3 |

**k = 10** (`manual_ablation_k10.md`):

| mode | precision@10 | recall@10 | all expected found | clean rate | forbidden hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.120 | 0.782 | 0.716 | 0.980 | 3 | 71.6 | 132.1 |
| hybrid | 0.129 | **0.878** | **0.832** | 0.980 | 2 | 87.3 | 133.6 |
| hybrid_rerank | 0.134 | **0.903** | **0.853** | 0.980 | 3 | 1283.8 | 2560.8 |

### 2.2 Margin over the dense baseline

| mode | k | Δ precision | Δ recall | Δ all expected found |
|---|---|---|---|---|
| hybrid | 5 | +0.009 | **+0.054** | **+0.063** |
| hybrid | 10 | +0.009 | **+0.096** | **+0.116** |
| hybrid_rerank | 5 | +0.008 | +0.056 | +0.063 |
| hybrid_rerank | 10 | +0.014 | **+0.121** | **+0.137** |

*Meaning.* Adding the sparse branch raises the share of turns whose every expected section reaches the
model from 68 % to 75 % at k=5 and from 72 % to 83 % at k=10. The reranker changes the top-5 order but not
its membership (+0.002 recall over hybrid); at k=10 it lifts recall a further 2.5 points.

### 2.3 Pass rate by capability (turn = all expected found and nothing forbidden)

| capability | n | dense k5 | hybrid k5 | rerank k5 | dense k10 | hybrid k10 | rerank k10 |
|---|---|---|---|---|---|---|---|
| behaviour: answer | 87 | 0.72 | 0.78 | 0.78 | 0.76 | 0.87 | 0.90 |
| behaviour: refuse | 12 | 0.17 | 0.25 | 0.25 | 0.17 | 0.25 | 0.25 |
| difficulty: easy | 25 | 0.80 | 0.80 | 0.84 | 0.84 | 0.88 | 0.92 |
| difficulty: medium | 47 | 0.70 | 0.77 | 0.77 | 0.74 | 0.79 | 0.87 |
| difficulty: hard | 28 | 0.43 | **0.54** | 0.50 | 0.43 | **0.71** | 0.61 |
| table_lookup | 26 | 0.81 | 0.89 | 0.85 | 0.85 | 0.92 | 1.00 |
| enumeration | 15 | 0.80 | 0.80 | 0.80 | 0.80 | 0.93 | 0.87 |
| ellipsis | 14 | 0.86 | 0.93 | 0.93 | 0.86 | 1.00 | 1.00 |
| multi_hop | 13 | 0.31 | **0.54** | 0.46 | 0.46 | **0.77** | 0.77 |
| policy | 10 | 0.30 | 0.30 | 0.30 | 0.30 | 0.40 | 0.50 |
| coreference | 8 | 0.75 | 0.62 | 0.75 | 0.75 | 0.88 | 0.88 |
| callout_text | 7 | 0.57 | 0.71 | 0.57 | 0.71 | 0.86 | 0.86 |
| unanswerable | 7 | 0.29 | 0.43 | 0.43 | 0.29 | 0.43 | 0.43 |
| single_hop | 6 | 0.67 | 0.50 | 1.00 | 0.67 | 0.67 | 1.00 |
| false_premise | 5 | 0.60 | 1.00 | 1.00 | 0.60 | 1.00 | 1.00 |
| image_ocr | 5 | 0.80 | 0.80 | 0.60 | 0.80 | 0.80 | 0.80 |

The full table (61 capability tags) is in the results files.

*Meaning.* Sparse matching helps most where the question names something concrete that lives in a
table or a specific section — priorities, SLA targets, service names, identifiers (`table_lookup`,
`multi_hop`, `false_premise`, `callout_text`). It does not help `policy` questions, which are phrased in
plain language far from the manual's wording. `refuse` turns score low by construction: a "pass" there
requires retrieving nothing forbidden, and the dataset forbids the topically-nearest sections (see 2.5).

### 2.4 Concrete sparse wins (dense missed an expected section; hybrid retrieved it)

11 turns at k=5, 13 at k=10, found by the harness. Selected examples:

| turn | query (standalone) | expected | dense recall → hybrid | why sparse wins |
|---|---|---|---|---|
| S03-T4 | If it does end up P2, what are we committed to? | 3.4 | **0.00 → 1.00** | "P2" is a table cell in §3.4; BM25 matches the token, cosine of the short question is closer to §3.3 |
| S07-T4 | sap-erp is 24/7 too, right? | 5.2 | **0.00 → 1.00** | `sap-erp` and `24/7` are exact tokens in the catalogue table |
| S08-T5 | What happens to an article nobody uses? | 12.1 | **0.00 → 1.00** | "uses, 12 mo" is a KB-header field explained in §12.1; the phrasing shares no synonyms with dense's neighbours |
| S05-T2 | I've got a copy of KB0010 here that says restart the app server. Should I just do that? | 6.13, 9.6 | 0.50 → 1.00 | `KB0010` and `restart` match the MIR actions in §9.6 as well as the article |
| S19-T5 | If a major incident manager tells me to do something this manual forbids, who wins? | Document control, 2.3 | 0.50 → 1.00 | "controlled document" phrasing in Document control is token-matched |
| S19-T2 | What changed between edition 3.2 and 4.0? | Document control | 0.00 → 1.00 (k=10) | edition numbers are tokens in the version-history table |
| S03-T3 | What two questions does the manual use to settle a priority dispute? | Appendix C | **0.00 → 1.00** | "impact" / "urgency" worksheet vocabulary |

Reverse cases exist and are in the file too: `coreference` at k=5 dropped 0.75 → 0.62 under hybrid (one turn
where a BM25 false friend displaced the right chunk from the top-5; recovered at k=10 and by the reranker).

### 2.5 The forbidden-section check (`clean`)

Two kinds of `must_not_retrieve` in the dataset, with different outcomes:

**Excluded content — the filter's job.** Turn S05-T1 (*"Order service is returning 500s with pool exhaustion.
What's the fix?"*) expects `6.13` and forbids `6.13 KB0010 v1`, the retired revision whose procedure caused
a 40-minute outage. The parser tags that revision `workflow_state=retired`; the existing pre-filter
(`ALLOWED_WORKFLOW_STATES=published`) excludes it inside every Qdrant search. **`6.13 KB0010 v1` appeared in
0 of 600 searches** (100 turns × 3 modes × 2 runs) across both k values, while `6.13 KB0010 v2` was ranked 1
for that turn in every mode. The archived scan (6.3) is excluded the same way.

**Topically adjacent content — not the filter's job.** The 2–3 forbidden hits per mode all come from two
`refuse` turns: S01-T5 (*remote-working policy for analysts* — forbids §2.1/§2.3) and S17-T4 (*printer
eating paper, weird noise* — forbids §6.7 the print-jobs article). Those sections are published, correct
content that happen to be the nearest neighbours of a question the manual cannot answer. Retrieval
returning them is expected; the dataset is testing whether the *answering* stage refuses rather than
paraphrases §2.1. That belongs to Sprint 3's generation/refusal logic. Clean rate 0.98 = 98/100 turns.

### 2.6 Latency

| mode | p50 ms | p95 ms | headroom vs 500 ms | within budget |
|---|---|---|---|---|
| dense | 58.9 | 79.6 | +420 | yes |
| hybrid | 79.0 | 114.2 | +386 | yes |
| hybrid_rerank | 1234.3 | 1868.3 | −1368 | no |

Same hardware as the base report (i5-1335U, 8 GB, CPU only). Hybrid's second Qdrant call costs ~20 ms at
p50. The reranker's cost is one cross-encoder pass per candidate (20) on chunks that are ~600 characters
including the heading prefix — longer than the KB chunks, hence slower than in Track A.

### 2.7 Determinism

`--runs 2` at k=5: rankings **IDENTICAL** in all three modes (`manual_ablation_k5.json: "deterministic": true`).
Same mechanisms as the base report: exact search, RRF on ranks with `k=60`, `(−score, point_id)` tie-break.

---

## 3. Interpretation and recommendation

1. **Hybrid earns its place on realistic content.** The base task's KB corpus was title-aligned and dense
   saturated it; the manual is not, and here every recall-type metric moves 5–12 points with the sparse
   branch, concentrated in `hard` and `multi_hop` turns. The mechanism is visible in the sparse-win list:
   tokens that live in tables (`P2`, `sap-erp`, `24/7`, edition numbers) are exact-match problems.
2. **The reranker is corpus-dependent.** Track A: +0.074 precision. Track B: no change at k=5, +2.5 recall at
   k=10, and 16–20× the latency. Keep it switchable; do not default to it on CPU.
3. **Recommendation stands:** `RETRIEVAL_MODE=hybrid` for the interactive path (p95 114 ms), with `k=10`
   worth considering for the manual corpus (all-expected-found 0.83 vs 0.75, precision cost 0.22 → 0.13).
4. **The filter generalises.** A second corpus with its own retired/archived content was excluded with no
   change to `filters.py` — because ingestion writes the same payload keys.

## 4. Limitations

- **Image-only content.** 5 turns (`image_ocr`) depend on text inside images (approval form, whiteboard
  photo). pymupdf extracts no text from them; those turns pass only when the surrounding section is
  retrieved. No OCR was attempted.
- **Refusal is not measured here.** 12 `refuse` and 1 `clarify` turns are scored on retrieval only; whether
  the system *answers* correctly is a generation metric (DeepEval/RAGAS adapters in `adapters.py` are ready
  for that stage).
- **`INC-TIME-01`** (dataset note): §10.4 states 16:24 and the approval-form image shows 09:41. Retrieval
  returns §10.4 for S10-T3; the conflict is for the answering stage to notice.
- **Section granularity.** Ground truth is per section; a chunk from the right section counts even if the
  specific sentence is in a different chunk of it. This slightly favours all modes equally.
- **One machine, CPU.** Latencies are indicative; ranking results are exact and machine-independent.

## 5. Reproduce

```bash
pip install -r requirements.txt                    # adds pymupdf
docker compose up -d qdrant
python -m src.retrieval.manual_parser --check eval/barq_rag_eval_dataset.json   # 76 sections, missing ids: none
python -m src.retrieval.ingest_manual                                          # barq_manual, 199 points
python -m src.retrieval.hybrid_search "Order service is returning 500s with pool exhaustion" \
       --mode hybrid --top-k 5 --collection barq_manual                        # 6.13 KB0010 v2 only, never v1
python eval/ablation_manual.py --k 5 --runs 2      # → eval/results/manual_ablation_k5.{md,json}
python eval/ablation_manual.py --k 10              # → eval/results/manual_ablation_k10.{md,json}
pytest -q                                          # 111 passed
```

Files: `src/retrieval/manual_parser.py`, `src/retrieval/ingest_manual.py`, `eval/ablation_manual.py`,
`eval/adapters.py` (mentor-supplied; only the dataset path was made relative to the file),
`eval/barq_rag_eval_dataset.json`, `tests/test_manual_parser.py` (6 tests), `eval/results/manual_ablation_*`.

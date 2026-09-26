# Sprint 2 — S2.6 RAG Hardening Report: Extractors, Stressor Ingestion & No-Regression Evidence

**Author:** Bassant Hossam, **Branch:** `s3.2/tool-registry-and-ocr`
**Companion:** [`sprint2_rag_hardening_readme.md`](sprint2_rag_hardening_readme.md) — what the extractors do and how to run them.

## Summary

Four results. The third is a regression this sprint caused, and it is reported as one.

1. **The extractors work, and the win is where the structure is.** Reading the manual as tables, forms and
   reading order instead of as lines of text takes hit@5 on 18 manual stressor rows from **0.722 to 1.000**
   and MRR from **0.616 to 1.000**. Five rows change hands, none the other way. The largest class gain is
   `page_crossing`, 0.000 → 1.000. (§4)
2. **With the manual's pages in the shared collection, all 18 stressor rows are answerable and the
   `combined` arm still answers all of them.** (§3)
3. **Adding the manual's pages to the live corpus costs 2 distinct baseline queries, and that cost is
   accepted** — 3 query-arm pairs, `INC1027` lost under `hybrid` and `hybrid_rerank`, `INC1011` lost under
   `hybrid_rerank` only. `dense` is unaffected. hit@5 goes 0.312 → 0.281 under `hybrid`, and to 0.250 under
   `hybrid_rerank` once OCR text is indexed. Both are the
   same pattern — a relevant manual or OCR point outranks the KB article that answers the query and takes
   its slot, and cannot itself satisfy an `expected_articles` row. **Rank displacement from corpus
   competition, not index corruption and not an extractor defect. No k change, no intent filter** — see §2.2
   for the decision and why. (§2)
4. **Two things do not work, and are documented rather than papered over.** The form extractor reports 57
   false fields off its home page, and every arm answers both deliberately-unanswerable rows. OCR does work
   and is measured against real Tesseract 5.3.4: 5 regions across 4 pages, 3 indexed, 2 correctly rejected by
   the confidence gate, and 10.4's approval form now readable as text. (§6)

### The correction that produced result 3

The first version of this sprint indexed the manual's extracted pages into a collection of their own,
`barq_manual_stressors`, and reported that baseline retrieval was byte-identical across all three modes
with no regression. **That result was not evidence.** With the manual in a separate collection, a baseline
query could not see a stressor point at all, so the no-regression check was structurally incapable of
failing — it would have passed with the extractors deleted. The check only becomes meaningful once the
manual's pages and the KB articles compete for the same top-k, which is also how the corpus will ship.

Both readings now live in `QDRANT.collection_name`, and the headline regression claim has been re-measured
from scratch against the corrected design. Result 3 is what that measurement found.

---

## 1. The design, and why it is this way

`ingest_manual.py` reads each page as a column of lines. That is the right reading for prose and the wrong
one for a table, a form or a screenshot. S2.6 reads the same 52 pages again through the extractors and
indexes 48 points from that output.

**The two readings go into the same collection as the KB articles.** `QDRANT.collection_name` now holds:

| content | points | flagged |
|---|---|---|
| KB articles (S1.4/S1.5) | 165 | — |
| model fingerprint marker (S1.4) | 1 | `_is_marker` |
| the manual's extracted pages (S2.6) | 48 | `is_stressor: true` |
| **total** | **214** | |

A manual page and a KB article therefore compete for the same five slots, which is the condition the
no-regression check needs and the condition production has. `RetrievalFilters.is_stressor` is what makes
the two separable for measurement without putting them back in separate collections:

| arm | filter | eligible |
|---|---|---|
| `baseline` | `is_stressor` absent | the KB articles |
| `stressor` | `is_stressor = true` | the manual's extracted pages |
| `combined` | none | everything, as a live query sees it |

`False` is expressed as `must_not: is_stressor = true`, not `must: is_stressor = false`, because a KB point
has no `is_stressor` key and Qdrant's `MatchValue` does not match a missing field — `must: false` would
return nothing at all.

`barq_manual` (the S2.4 line reading, 199 points) is left exactly as it was. It is an *alternative
indexing of the same pages* for the extractor-quality comparison in §4, not additional live content, and
folding it in as well would double-count the manual.

### 1.1 Entry point

Stressor ingestion is reached through `ingest.py`, alongside the other two write paths:

```python
from src.retrieval.ingest import ingest_articles, sync_kb, ingest_stressors, drop_stressors
ingest_stressors()      # -> {"points": 48, "collection": "barq_knowledge_base", "shared_with_kb": True, ...}
drop_stressors()        # removes only is_stressor points
```

```
python -m src.retrieval.ingest stressors
python -m src.retrieval.ingest drop-stressors
```

`ingest_stressors.py` holds the routing, page→capability mapping and point building. It is a module the
entry point calls, not a second entry point.

### 1.2 Two hazards that sharing a collection creates, and their handling

Both were found by writing the code, not by a failing test, and both are now tested.

**`sync_kb()` would have deleted all 48 manual points.** `_stored_hashes()` reads `article_id` and
`content_hash` for everything in the collection and `sync_kb()` deletes any `article_id` that is no longer
in ServiceNow. A stressor point carries an `article_id` (the section label, e.g. `7.1`) and no
`content_hash` — indistinguishable from an article that disappeared upstream. The first `sync_kb()` after
this sprint would have wiped the manual. `_stored_hashes()` now skips `is_stressor` points, and
`_delete_article()` carries `must_not: is_stressor = true` so that a section label colliding with an
article id cannot take the other one down either.

**A manual run could create a collection with the wrong vectors.** Collection creation stays with
`ingest.py`'s `_ensure_collection()`, which checks the embedding-model fingerprint before writing. A
stressor run cannot bring up a collection with a different vector size, distance or model, which is the
failure mode that silently splits a corpus in two. `tests/test_ingest_stressors.py` asserts that
`ingest_stressors` never calls `create_collection`.

---

## 2. No-regression evidence — the real check

`python eval/ablation.py --regression` →
[`eval/results/regression_k5.md`](results/regression_k5.md), [`eval/results/regression_k5.json`](results/regression_k5.json)

The 40 baseline rows, k=5, scored twice in one pass against one collection. The `is_stressor` filter is the
only difference between the two columns, so the comparison cannot be confounded by drift in the corpus or
the index. (`kb_only` is equivalent to physically removing the points: the filter is applied inside Qdrant
before ranking, so it returns the same KB points in the same order a KB-only collection would.
`python -m src.retrieval.ingest drop-stressors` is the physical equivalent and doubles as the rollback.)

**All figures in this table are out of the 32 answerable baseline rows** (`baseline_rows = 37`,
`answerable_rows = 32`; the 5 unanswerable rows carry no `expected_articles` and can neither gain nor lose
a hit). The `lost` column counts **query-arm pairs**, not distinct queries — see the note under the table.

| mode | hit@5 (KB only) | hit@5 (manual mixed in) | Δ | MRR (KB only) | MRR (mixed) | lost (query-arm pairs) | gained | verdict |
|---|---|---|---|---|---|---|---|---|
| `dense` | 0.281 | 0.281 | +0.000 | 0.250 | 0.250 | 0 | 0 | held |
| `hybrid` | 0.312 | 0.281 | **−0.031** | 0.258 | 0.245 | **1** | 0 | **REGRESSION, accepted** |
| `hybrid_rerank` | 0.312 | 0.250 | **−0.062** | 0.232 | 0.214 | **2** | 0 | **REGRESSION, accepted** |

**Answer to the question asked: baseline hit@5 did not hold steady. It dropped by one query in 32 under
`hybrid` and by two under `hybrid_rerank`, and held exactly in `dense`.**

**2 distinct queries lost, 3 query-arm pairs.** The `lost` column sums to 3 because `INC1027` is lost under
*both* fusion modes: `INC1027` is lost under `hybrid` and `hybrid_rerank`; `INC1011` is lost under
`hybrid_rerank` only. Both are named, traced and accepted as one pattern in §2.1–§2.2.

Arithmetic, for anyone checking: kb_only resolves 9 / 10 / 10 hits out of 32 and mixed resolves 9 / 9 / 8, so
the losses are 0, 1 and 2, and `gained` is 0 in every arm — net loss, not redistribution.
`eval/results/ablation_k5.{json,md}` has been regenerated and now records the *live* corpus (unfiltered), so
its `hybrid` and `hybrid_rerank` hit@5 read 0.281 and 0.250. That file previously held the pre-sprint 0.312
as "no regression" evidence; it no longer claims that.

**MRR does not follow from the lost list alone,** and the difference is the same crowding seen from the other
side. Losing a query that was hit at rank *r* removes `1/r` from the MRR numerator, but queries that keep
their hit while sliding down the ranking cost MRR too. For `hybrid`, `INC1027` at rank 4 accounts for
−0.0078 MRR directly, against −0.013 observed; the ~0.167-point residual is spread across the 13 queries
where a manual point took a slot but the expected article stayed in the top 5. For `hybrid_rerank` the two
lost queries at ranks 3 and 4 account for −0.0182 against −0.018 observed — fully accounted, within
rounding. So MRR moves further than the hit count under `hybrid` precisely because rank movement among
surviving queries is not free.

### 2.1 The two queries that changed

One pattern, two instances: a manual or OCR point that is *relevant* scores above the KB article that
*answers* the query, takes its slot, and cannot itself satisfy an `expected_articles` row because a manual
section carries no KB number. Both are rank displacements, not index corruption and not extraction defects.

**`INC1027`** — *"New starter locked out on day one and also cannot see the finance shared folder"* — expects
`KB0005` and `KB0020`.

| condition | top-5 | first hit |
|---|---|---|
| `hybrid`, KB only | `7.4`, `KB0003`, `6.2`, **`KB0005`** | rank 4 |
| `hybrid`, manual mixed in | `7.4`, `KB0003`, `6.2`, **`''`** | none |
| `hybrid_rerank`, KB only | `3.2`, `7.4`, `6.2`, **`KB0005`** | rank 4 |
| `hybrid_rerank`, manual mixed in | **`''`**, `3.2`, `7.4`, `6.2` | none |

**`INC1011`** — *"User locked out and also cannot connect VPN after password change this morning"* — expects
`KB0005` and `KB0001`. This one appeared only once OCR text was indexed.

| condition | top-5 | first hit |
|---|---|---|
| `hybrid_rerank`, KB only | `3.2`, `7.2`, **`KB0005`**, `7.4` | rank 3 |
| `hybrid_rerank`, OCR text indexed | **`''`**, `3.2`, `7.2` | none |

The displacing point is the OCR region for **7.1's incident form** (page 25, Tesseract confidence 86.01), taken
at rank 1 with score 5.67. It is worth being precise about why, because it is the whole argument for
accepting it: **that read is correct.** Its text describes a locked-out user, which is exactly what the query
asks about, and 86.01 is a confident read. The reranker is not malfunctioning — it is ranking a relevant
document highly. The failure is narrower and different in kind: the region cannot satisfy an
`expected_articles` row (no KB number), so it consumes the slot that `KB0005` was holding at rank 3.

**Raising `MIN_OCR_CONFIDENCE` would not fix this, and would be the wrong fix.** The offending region scores
86.01; no threshold below that excludes it, and a threshold above it discards a correct read along with 10.4's
approval form. The defect, to the extent there is one, is in ranking and slot allocation, not in OCR quality
— the extractor is doing its job in both cases.

(To be precise about two things that are easy to conflate: the displacing point is characterised by its
empty **`number`**, which is what makes it ungradable here. Its absent **`content_hash`** is a different
fact with a different consequence — that is what made `sync_kb()` treat it as a deleted article, §1.2. And in
the de-duplicated lists above the displacement is at rank 3 or 4, not the 5th slot; the raw top-5 contains
duplicate article numbers, so the unique rank and the slot number differ.)

This is **slot-crowding from increased corpus competition.** The same 48 points answer 18 manual-section
questions that the live corpus could not answer at all before this sprint (§3), and 3 of those are now
answerable from text that was previously only recorded as a region. The effect is scoped by the slot
histogram.

**This histogram is out of all 40 baseline rows, not the 32 answerable ones** — the counter increments for
every row before the hit comparison, including the 8 unanswerable rows (`eval/ablation.py:487`). It is
deliberately the wider base: the question it answers is "how often does a manual point occupy a slot at
all", which is meaningful for an unanswerable row too. **Do not compare it to the table above**, which is
out of 32.

| mode | queries where a manual point took 1 top-5 slot (of 40) | 2 or more |
|---|---|---|
| `dense` | 5 / 40 | 0 |
| `hybrid` | 14 / 40 | 0 |
| `hybrid_rerank` | 28 / 40 | 0 |

`hybrid_rerank` is hit hardest because its cross-encoder stage promotes the manual page, and `dense` barely
moves because it has no fusion step to promote anything. No query gains a hit in either mode, so this is
net −2 distinct queries, not a redistribution.

### 2.2 The decision: accepted, no k change, no intent filter

Reviewed and decided at mentor review, twice — once for `INC1027` and again after OCR was measured against
real Tesseract and `INC1011` appeared. Both stand as measured.

**Accepted trade: 2 distinct baseline queries out of 32** — 3 query-arm pairs, since `INC1027` is lost under
`hybrid` and `hybrid_rerank` and `INC1011` is lost under `hybrid_rerank` only — against 18 manual-section
questions that move from unreachable in the live corpus to answered, the `stressor` arm at precision@5 0.519,
recall@5 1.000, hit@5 1.000 (§3). **Five of the eighteen are gained by extraction work alone** (0.722 → 1.000
on 18 rows is 13 answered by the line reading against 18, so five change hands and none change back, §4);
the approval form on 10.4 is now readable as text rather than only as a located region, which is a capability
that did not exist before this sprint. The corpus is strictly more informative and two retrieval slots on two
queries is less informative.

`INC1011` is accepted on the same reasoning as `INC1027`, and for the same reason it is not a quality defect:
the point that displaces the article is a *correct* read of a *relevant* document. Trading one query's rank
order for the ability to read 10.4 at all is the same trade as trading one for 18 section-level answers,
scaled down. If the two had opposite signs — if the displacing point were garbage — the decision would be
different, which is why the 55 gate exists and why the two rejected regions (22.3, 30.7) are not in this
discussion.

**`k=5` is unchanged, deliberately.** It is not a parameter this sprint owns: `QDRANT`/`RETRIEVAL.top_k` is
shared, and S2.4 and S2.5 results are both quoted at k=5. Raising it to 10 would very likely recover both
queries — `KB0005` sat at unique-rank 3–4 in each before the manual was added, immediately adjacent to the
displacing point — but it changes the retrieval contract for every caller in the system and invalidates the
comparative baselines those sprints reported. Assessed this close to the deadline, that is more downstream
risk than the two queries it buys, so it was not done.

**No intent filter.** `RetrievalFilters(is_stressor=...)` would restore 0.312 exactly and is a one-line
change, but it pushes a routing decision upstream of retrieval and gives up the property that made this
measurement possible in the first place — that a live query sees one corpus. Kept as a documented option for
whoever owns the retrieval contract, not adopted here.

**What would revisit this:** if the manual's 48 points grow substantially, or if further KB articles are
added, the crowding compounds and the same argument stops holding — a third displacement would be a trend,
not a cost. The check is a repeatable command — `python eval/ablation.py --regression` — so the next person
to touch this does not have to reconstruct it.

---

## 3. Three-arm stressor results on the shared collection

`python eval/ablation.py --stressors` →
[`eval/results/stressors_k5_hybrid.md`](results/stressors_k5_hybrid.md), [`eval/results/stressors_k5_hybrid.json`](results/stressors_k5_hybrid.json)

18 answerable rows of 20 (`STR-19`, `STR-20` are intentionally unanswerable and are not scored), k=5,
`hybrid`:

| arm | eligible | precision@5 | recall@5 | hit@5 | MRR | answered-unanswerable | p95 ms |
|---|---|---|---|---|---|---|---|
| `baseline` | KB articles only | 0.000 | 0.000 | 0.000 | 0.000 | 2/2 | 2322.0 |
| `stressor` | manual pages only | 0.519 | **1.000** | **1.000** | **1.000** | 2/2 | 949.4 |
| `combined` | everything | 0.491 | **1.000** | **1.000** | 0.778 | 2/2 | 2407.1 |

`baseline` scoring 0.000 is the expected reading, not a defect: the KB articles carry no manual section
labels, so a section-level question can only be answered by a manual point. This table answers *whether the
manual is reachable in the live corpus at all*. It does not measure extractor quality — that is §4, and the
two must not be confounded.

`combined` matches on hit and recall but drops to 0.778 MRR (§6.5): with KB articles also in the candidate set,
the correct section is sometimes pushed below a KB article that is topically close. This is the same
crowding mechanism as §2.1, visible from the other side, and it is the cost of not filtering by intent.
Before OCR text was indexed this figure read 0.806; the 3 confident OCR regions are the difference, and the
move is the same accepted trade as §2.1, not a new failure.

---

## 4. Extractor quality vs the line reading

This is a different question from §2 and §3, and it needs a different pair of corpora. Both readings of the
manual exist — `barq_manual` (lines, S2.4) and the 48 extracted pages (S2.6) — and they can be compared
directly because neither is additive live content.

18 answerable rows, k=5, `hybrid`, re-measured for this report:

| reading | collection | precision@5 | recall@5 | hit@5 | MRR |
|---|---|---|---|---|---|
| lines of text | `barq_manual` | 0.200 | 0.722 | 0.722 | 0.616 |
| tables, forms, reading order | shared, `is_stressor = true` | **0.519** | **1.000** | **1.000** | **1.000** |

### 4.1 Per capability class

| capability | rows | line reading hit@5 | extractor hit@5 | delta |
|---|---|---|---|---|
| `page_crossing` | 2 | **0.000** | **1.000** | **+1.000** |
| `key_value` | 2 | 0.500 | 1.000 | +0.500 |
| `layout` | 2 | 0.500 | 1.000 | +0.500 |
| `table` | 4 | 0.750 | 1.000 | +0.250 |
| `merged_header` | 3 | 1.000 | 1.000 | +0.000 |
| `nested_table` | 2 | 1.000 | 1.000 | +0.000 |
| `ocr` | 2 | 1.000 | 1.000 | +0.000 |
| `form_parsing` | 1 | 1.000 | 1.000 | +0.000 |

**`page_crossing` is the finding.** 7.1's journal runs off the foot of p25 and resumes at the top of p26
under a repeated header. P26 is the first page of no section, so a line reading of p25 returns 7.1 without
the closing entries and a line reading of p26 returns nothing useful — the line reading scores 0.000 on both
`page_crossing` rows. The extractor stitches the halves, records `spans_page_boundary: true` and both page
numbers, and both rows become hits.

**`merged_header`, `nested_table`, `ocr` and `form_parsing` score 1.000 in both readings.** Their section
headings survive line extraction, so the baseline already finds those sections. These eight rows are
**regression guards, not wins** — they would catch an extractor that made 2.1, 5.2, 11.7 or 10.4 *worse*,
and they do not demonstrate a gain. Reporting them as an 8-row improvement would be reading the table wrong.

### 4.2 What changed hands

| row | query | class | line reading returned | extractor returned | hit came from |
|---|---|---|---|---|---|
| `STR-06` | last entry in the INC0010023 journal, and what time | page_crossing | 7.2, 7.3 | **7.1** | tables, layout |
| `STR-07` | which work note closed the VPN incident, and how long | page_crossing | 7.2, 7.4, 7 | **7.1**, 3.3 | tables, layout |
| `STR-10` | SLA breach on INC0010023, against what target | key_value | 7.2, Appendix A, 7.5, 3.4 | **7.1**, 12.2 | tables, layout |
| `STR-14` | which agent stages screen incident text for embedded instructions | layout | 11.6, 11.2, 11.3 | **11.7**, 11.8, 12.2 | tables, layout |
| `STR-15` | contractual SLA attainment measure, and what it is used for | table | 12.1, Appendix A | **12.2**, 3.3, Appendix C | tables |

Gained 5, lost 0, of 18. Every gain is the line reading landing on a *neighbouring* section — 7.2 and 7.3
instead of 7.1, 11.2 and 11.6 instead of 11.7 — which is the expected failure: the prose continues past the
table, so a line reading ranks the section after the table above the table's own section.

Every quality figure in this section reproduced exactly across two independent measurements. Only the timing
columns moved.

---

## 5. Latency

The p95 columns above are not measuring the extractors. Breaking one query down:

| stage | ms |
|---|---|
| `embed_dense(query)` — one HTTPS POST to `LITELLM_BASE_URL` | 845–1317 |
| `embed_sparse(query)` — local BM25 | 0.1–0.4 |
| Qdrant dense search, 20 candidates | 8–20 |
| Qdrant sparse search, 20 candidates | 4–7 |

**Dense embedding is a synchronous network round-trip, not a local model call.** `embedding.py` posts one
request per `embed_dense` call, and every retrieval mode starts with one. That single call is 97–99% of
query latency and is the whole reason the 500 ms budget is missed: the Qdrant work, including the second
search that `hybrid` adds, is 12–27 ms combined.

The three arms' latency is the same within noise, which is the point: the extractors are index-time work,
and the shared collection costs no extra query latency, only the `is_stressor` filter and one more 48 points
in a 214-point index. The absolute p95 is not stable between runs, because the term that dominates it is
the remote embedding round-trip (§5, 97–99% of query latency), which this sprint does not control. The run
whose numbers are in `eval/results/stressors_k5_hybrid.md` reports 949 ms for `stressor` against 2322 ms for
`baseline` and 2407 ms for `combined`; an earlier run of the same command reported 928 / 998 / 1049 ms. The
ordering flips between the two runs, which is what "within noise" means here — and it is worth being explicit
that neither run shows the extractors adding query latency, which is the only claim this section makes. An
even earlier run reported a 6043 ms p95; that outlier was one `STR-10` call whose embedding round-trip took
6.1 s, and it did not recur.

**Recommendation (unchanged from S2.4, and now with a cause):** the budget needs the embedding call
addressed, not retrieval. Batching is not available — there is nothing else to batch a single query
embedding with — so the options are a local or self-hosted embedding model, a keep-alive connection pool
around `httpx.post`, or raising the budget to match the remote latency. Out of S2.6's scope; recorded here
because both this report and the S2.4 report quote p95 numbers that mean something different from what
they appear to.

**Index-time cost:** 48 points, 24 tables and 11 pages of layout extraction, plus 48 embedding calls,
measured at 64 s for the full run.

---

## 6. Known limits and accepted trades

### 6.1 OCR: measured against real Tesseract, and accepted

`extractors/ocr.py` came from `origin/feat/Sprint-3-(S3.3)---Input-&-Output-Guardrails-&-Ocr` (S3.3, Marcelino).
`ingest_stressors.py` renders each image region off the page with PyMuPDF's `get_pixmap` and reads it through
`extract_ocr_from_image` — so the ingestion path needs neither pdf2image nor Poppler. 5 regions across 4 pages
(**25, 35, 42, 44** — not 3 pages, as an earlier draft of this report said), routed by `page_needs_ocr`.

**Every figure below is measured against real Tesseract 5.3.4 with pytesseract 0.3.13, not projected and not
mocked.** An earlier draft of this report stated the opposite — that the numbers here were collected with no
`tesseract` binary on the box and that the applied path was therefore unexercised. Tesseract is now
installed, the pipeline was re-ingested, and §2 was re-measured against the result.

| page | confidence | chars | outcome |
|---|---|---|---|
| 25 (7.1 incident form) | 86.01 | 641 | indexed |
| 35 (10.4 approval form) | 85.02 | 337 | indexed |
| 42 (worker log) | 78.97 | 968 | indexed |
| 42 | 22.33 | 57 | read, rejected — below the 55 gate |
| 44 | 30.74 | 211 | read, rejected — below the 55 gate |

The gate earns its keep on the last two: tesseract returned text, and it was wrong enough not to be worth
indexing. A region is always indexed either way, with `ocr_applied` and a `reason`, so nothing is dropped
silently.

**10.4's approval form is no longer unanswerable.** Page 35 reads at 85.02 confidence and is indexed as
text. It was previously a located region with no readable contents, and it is now one of the 18
manual-section questions the live corpus can answer.

**What it costs.** `hybrid_rerank` loses one further baseline query, `INC1011`, taking hit@5 from the
accepted 0.281 to **0.250** (MRR 0.232 → 0.214) with `INC1027` still lost. `dense` holds at 0.281 and
`hybrid` still loses exactly the one accepted query. The full before/after and the root cause are in §2.1;
the short form is that 7.1's incident form, read at 86.01 confidence, is a *correct* read of a *relevant*
document that takes rank 1 ahead of `KB0005`.

On the manual side `--stressors` is flat where it matters and slightly down where it does not: `hit@5` and
`recall@5` stay 1.000 in the `stressor` and `combined` arms, `combined` MRR moves 0.806 → 0.778.

**Raising `MIN_OCR_CONFIDENCE` is not the fix and was not adopted.** The offending region scores 86.01; no
threshold below that excludes it, and one above it throws away a correct read together with 10.4's form. The
issue is ranking and slot allocation, not OCR quality, so tuning the extractor would be treating the wrong
layer.

**Decision: accepted, mentor-approved, on the same grounds as `INC1027`.** Both losses are instances of one
pattern — mixed-corpus retrieval trading a small number of specific, understood rank displacements for
genuine coverage gains. The net: **2 distinct baseline queries lost** — (3 query-arm pairs: `INC1027` lost under `hybrid` and `hybrid_rerank`; `INC1011` lost under `hybrid_rerank` only) — against **18
manual-section questions gained**, among them an approval form that was not readable as text before this
sprint. `k=5` stays, no intent filter is introduced, and the reasoning is set out in full in §2.2.

### 6.2 The form extractor over-fires off 7.1

71 form fields are extracted across the 11 routed pages, of which 14 are 7.1's actual incident form. The
other 57 are table rows on 12.2, 11.7, 5.2, 3.3, 2.1 and 8.3 that read as label-above-value pairs. On p25
the extractor produces all 14 fields correctly (`Number: INC0010023`, `Impact / Urgency: 3 – Individual /
2 – Medium`, `Breach: None · 48 min against a 3-day target`) and rejects bullets, folios, record IDs and
table headers as keys — so the tuning is right for its target and simply not yet fenced off from pages that
happen to have the same shape.

Retrieval is unaffected (the layout and table points for those pages carry the real content), but a caller
trusting `form_fields` on 12.2 is reading table rows. The fix belongs in the router, which already knows
which pages have ruled grids; it is left for the sprint that owns form extraction rather than papered over
with a threshold that would also suppress 7.1's 14 real fields.

### 6.3 Every arm answers both unanswerable rows

`STR-19` (sensor-fleet resolution target) and `STR-20` (retention period) return results in every arm. The
manual does not cover either, and retrieval has no refusal mechanism. `answered_unanswerable` is reported
rather than scored, because scoring it as a miss would say the retrieval failed when what is missing is a
threshold at the answer layer. This is the same boundary the S3.3 output guardrails address.

### 6.4 The checkbox detector finds nothing, correctly

Zero checkboxes across 52 pages. 10.4's approval form is a raster screenshot, so its checkboxes exist as
pixels; a detector reporting boxes there would be reading compression artefacts. `Checkbox.as_dict()`
exists and the detector is tested against synthetic pages, but on this manual there is nothing honest to
report.

### 6.5 `combined` MRR is below the `stressor` arm

0.778 against 1.000 (§3), moved from 0.806 by the same accepted trade documented in §6.1. Same mechanism as
the §2.1 regressions seen from the other side: KB articles in the candidate set push the correct manual
section down. It is reported rather than tuned away, and it falls under the same accepted trade (§2.2) — the
`combined` arm is not the shipping configuration, and no intent filter was adopted to protect it.

---

## 7. Tests

| file | what it pins | count |
|---|---|---|
| `tests/test_extractors_tables.py` | merged headers, nested attachment, split-table merge, prose rejection, unique columns, provenance, the capability/trait vocabulary split | 21 |
| `tests/test_extractors_layout.py` | column ordering, header/footer removal, form pairing and rejection, OCR gate, checkbox false-positive guards | 23 |
| `tests/test_ingest_stressors.py` | routing decisions and reasons, split-claim of p26, payload contract, deterministic ids, shared-collection targeting, no `create_collection`, drop-is-a-filter, `sync_kb` leaving manual points alone, `_delete_article` sparing them | 37 |
| `tests/test_filters.py` | the `is_stressor` filter: arms mutually exclusive, `combined` unconstrained | 5 |
| `tests/test_ablation.py` | `grade()` and `sparse_wins()` (unchanged), plus `summarise_arm`, `arm_regressions`, `quality_fingerprint`, and that the arms are filters over one collection | 17 |

Four guards protect against tests that pass for the wrong reason, and three were added after a test
demonstrably did:

- `test_synthetic_grids_are_detected_by_find_tables` — three split-detection tests were green because
  `find_tables` could not see a grid drawn with only its outer box, so `_continues_onto` returned `False`
  because it saw no tables. The helper now rules every cell edge and puts text in every cell, and this
  test fails if that ever regresses.
- `test_every_declared_class_is_one_the_extractors_can_produce` — `STRESSOR_SECTIONS` declared
  `dark_theme`, `rotated_image` and `arabic_rtl` as capability classes, and nothing in the codebase produces
  any of them. They now live in `IMAGE_TRAITS`, which is what a test row for an extractor that does not
  exist looks like.
- `test_sync_kb_does_not_delete_stressor_points` and `test_delete_article_cannot_reach_a_stressor_point` —
  the two §1.2 hazards. Both were latent in the design before they were tests.

`test_real_splits_detected_are_exactly_the_four` pins the heuristic to `[25,26] [31,32] [47,48] [51,52]`
and nothing else in the 52 pages.

---

## 8. Changes

**Added**

- `src/retrieval/extractors/tables.py` — table reconstruction
- `src/retrieval/extractors/layout.py` — reading order, forms, OCR gate
- `src/retrieval/extractors/__init__.py` — public surface, lazy OCR import
- `src/retrieval/ingest_stressors.py` — per-page routing and point building (module, not entry point)
- `tools/build_coverage_matrix.py` — regenerates the CSV from the S1.4 baseline
- `data/coverage_matrix_s14_baseline.csv` — the baseline as it was at `d9b1be7^`
- `eval/results/regression_k5.{md,json}` — the corrected no-regression evidence
- `tests/test_extractors_tables.py`, `tests/test_extractors_layout.py`, `tests/test_ingest_stressors.py`
- `docs/sprint2_rag_hardening_readme.md`, `docs/sprint2_rag_hardening_report.md`

**Modified**

- `src/retrieval/ingest.py` — **the deliverable entry point.** Adds `ingest_stressors(source="manual")`
  and `drop_stressors()` beside `ingest_articles()` and `sync_kb()`, and the `stressors` /
  `drop-stressors` CLI verbs. `_stored_hashes()` skips `is_stressor` points and `_delete_article()` carries
  `must_not: is_stressor = true`, so `sync_kb()` can no longer delete the manual's pages.
- `src/retrieval/filters.py` — `RetrievalFilters.is_stressor`, which is what makes the arms filters
  instead of collections.
- `src/retrieval/ingest_stressors.py` — writes to `QDRANT.collection_name`; `STRESSOR_COLLECTION` is now
  an alias for it rather than a second collection. Adds `upsert_stressor_points()`,
  `drop_stressor_points()`, `count_stressor_points()`. No longer creates a collection.
- `src/retrieval/extractors/tables.py` — `STRESSOR_SECTIONS` corrected: `page_crossing` belongs to 7.1, not
  8.3. 8.3 reads as one register and is two. `dark_theme` / `rotated_image` / `arabic_rtl` / `checkbox` moved
  out of the capability vocabulary into a new `IMAGE_TRAITS`.
- `src/retrieval/extractors/layout.py` — `Checkbox.as_dict()`
- `data/coverage_matrix.csv` — 27 baseline rows byte-identical, 20 stressor rows added (47 total)
- `eval/evaluation_set.json` — v1.1 → v1.2, 37 → 57 rows, v1.1 items byte-identical
- `eval/build_evaluation_set.py` — reads the extended schema, maps both `source` values explicitly
- `eval/ablation.py` — `MANUAL_ARMS` as filters, `run_stressors`, `run_regression`, `regression_markdown`,
  `run_mode(..., filters=)`, and the `--regression` flag. `grade()` and `sparse_wins()` untouched.
- `eval/results/ablation_k5.{json,md}` — regenerated at v1.2 against the **live** corpus, so `hybrid` and
  `hybrid_rerank` now read hit@5 0.281. This file no longer claims no regression.

**Not touched**

- `requirements.txt` — PyMuPDF only, no new dependency
- `barq_manual` — the S2.4 line-reading collection, left as the alternative indexing for §4

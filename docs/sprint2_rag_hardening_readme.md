# Sprint 2 — S2.6 RAG Hardening: Document Extractors, Stressor Ingestion & Evaluation

**Author:** Bassant Hossam, **Branch:** `s3.2/tool-registry-and-ocr`

## Summary

`manual_parser.py` reads a PDF page as a column of lines. That is correct for prose and wrong for
everything the BARQ manual was built to stress: a table read line by line loses the row it belongs to, a
form read line by line loses the label sitting above the value, and a screenshot has no lines at all.

S2.6 adds two extractors (`extractors/tables.py`, `extractors/layout.py`), a per-page router that decides
which extractor a page can actually use, and a second reading of the manual built from their output. All 48
resulting points go into **`QDRANT.collection_name` — the same collection as the KB articles** — flagged
`is_stressor: true`, so a manual page and a KB article compete for the same top-5 slots exactly as they
will in production.

That shared search space is what makes the measurement mean anything, and it also costs something. Both
results below are real, and the second one is a regression this sprint introduced:

| three arms over one collection | eligible points | hit@5 (18 stressor rows) | MRR |
|---|---|---|---|
| `baseline` | KB articles only | 0.000 | 0.000 |
| `stressor` | the manual's extracted pages only | **1.000** | **1.000** |
| `combined` | everything, as a live query sees it | 1.000 | 0.778 |

All figures below are out of the **32 answerable rows** of 40; the 8 unanswerable rows carry no
`expected_articles` and can neither gain nor lose a hit.

| 40 baseline KB rows, k=5 | hit@5 (KB only) | hit@5 (manual mixed in) | verdict |
|---|---|---|---|
| `dense` | 0.281 | 0.281 | held |
| `hybrid` | 0.312 | 0.281 | **−1 query (INC1027)** |
| `hybrid_rerank` | 0.312 | 0.250 | **−2 queries (INC1027, INC1011)** |

**This 2-distinct-query regression is accepted, not fixed** — 3 query-arm pairs, since `INC1027` is lost
under both fusion modes and `INC1011` under `hybrid_rerank` only, so the two regression rows above sum to 3.
It is one pattern twice over: a relevant manual or OCR point outranks the KB article that answers a query,
takes its slot, and cannot itself satisfy an `expected_articles` row because a manual section carries no KB
number. Manual pages take a top-5 slot on
14/40 baseline queries under `hybrid` and 28/40 under `hybrid_rerank`, but only 5/40 under `dense` — which is
why `dense` is untouched. The second loss, `INC1011`, appeared only once OCR text was indexed; the
displacing point is 7.1's incident form read at 86.01 confidence, which is a *correct* read of a *relevant*
document, so the extractor is not at fault and raising the confidence gate would only discard good text.
`k=5` was deliberately left alone: it is a shared retrieval parameter that S2.4 and S2.5 also depend on, and
changing it this close to the deadline was assessed as more downstream risk than the benefit. **2 distinct
queries lost against 18 manual-section questions gained**, among them 10.4's approval form, which is now readable as
text rather than only as a located region. The full reasoning is in
[`sprint2_rag_hardening_report.md`](sprint2_rag_hardening_report.md) §2.

**No new dependencies.** PyMuPDF only — no `pdf2image`, no Poppler.

---

## 1. Running it

```bash
# Qdrant: the hostname `qdrant` in .env does not resolve from the host
export QDRANT_URL=http://localhost:6333

# index the manual as lines of text (the S2.4 baseline; already indexed)
python -m src.retrieval.ingest_manual

# index the same manual through the extractors, into the KB collection
python -m src.retrieval.ingest stressors          # entry point: ingest.py
python -m src.retrieval.ingest drop-stressors     # rollback: removes only the manual's pages

# KB half: the S2.4 harness, now measuring the corpus as it will ship
python eval/ablation.py

# the no-regression check: the same 37 rows, with and without the manual eligible
python eval/ablation.py --regression

# manual half: three filters over the one collection
python eval/ablation.py --stressors
python eval/ablation.py --stressors --stressor-mode hybrid_rerank --k 10

# the 20 stressor rows
python eval/build_evaluation_set.py        # data/coverage_matrix.csv -> eval/evaluation_set.json
python tools/build_coverage_matrix.py      # the S1.4 baseline + the stressor rows -> the CSV
```

Inspect one page by hand:

```bash
python -m src.retrieval.extractors.tables      # every table in the document
python -m src.retrieval.extractors.layout      # reading order, forms, OCR regions, per page
```

---

## 2. The extractors

### 2.1 `extractors/tables.py`

`find_tables` gives a grid with 9 columns on a page where the real table has 5, because a cell that spans
two header rows is read as two columns of slivers. The extractor works on the ruled lines themselves rather
than on `find_tables`' cell list:

| problem in the manual | what the extractor does |
|---|---|
| merged header over sub-columns (2.1, 3.3, Appendix C) | finds header bands by cut voting across rows, then joins each spanned header with its sub-header: `COVERAGE` + `HOURS (GST)` → `DUBAI HOURS (GST)` |
| nested `FIELD/VALUE` tables inside a cell (5.2, 11.7) | detects an inner grid inside a cell, attaches it to the cell, renders it inline, and does not index it a second time |
| a table split across a page break (7.1: p25 → p26) | stitches the halves when the next page's first table continues the previous page's last, and records `spans_page_boundary` and both page numbers |
| repeated column names ("Notes" twice) | suffixes them (`Notes`, `Notes (2)`) so `as_records()` does not silently lose a column |
| a cell that is a sentence, not a table (callout boxes on 2.1) | drops a grid with no data rows rather than indexing a box of prose as a one-row table |
| a row that wraps across two physical rows | joins it back using the ruling lines, not the text |

Public surface:

```python
extract_page_tables(page, page_number) -> ExtractedTable
extract_tables(pdf_path=None, page_numbers=None, strategy="lines",
               sliver_width_pt=SLIVER_WIDTH_PT, merge_across_pages=True) -> TableExtractionResult
```

`TableExtractionResult` carries `.tables`, `.page_numbers` and `.provenance` (`table_count`,
`nested_table_count`, `page_crossing_tables`, `pymupdf_version`, `source_file`). Each `ExtractedTable`
carries `confidence`, `issues`, `method`, `has_merged_header`, `has_nested`, `spans_page_boundary`, and
renders through `to_markdown()` / `to_text()` / `as_records()`.

Cross-page merging needs a contiguous page range. `extract_tables(page_numbers=[25, 27])` cannot stitch a
table whose middle page is missing, and returns the halves as two tables — deliberately, because a merged
table with a hole in it is a lie.

### 2.2 `extractors/layout.py`

| problem | what the extractor does |
|---|---|
| two-column pages read left-to-right across both (11.7, Appendix C) | detects column bands from block geometry and orders blocks column-first, top-to-bottom within each column |
| the incident form on 7.1: labels above values | pairs a label with the value beneath it; rejects bullets, folios, record IDs and table headers as keys |
| a screenshot with no text layer (10.4, 11.8, 11.9, 12.2) | reports the image region and its pixel size instead of pretending the page is empty |
| running headers and footers | removed by content (repeats across pages, short, above/below the text block) rather than by y-position, so a body line at the top of the page survives |

```python
extract_layout(page, page_number) -> LayoutExtractionResult
extract_layout_document(doc, page_numbers) -> list[LayoutExtractionResult]
page_needs_ocr(page) / page_has_text_layer(page) -> bool
```

`LayoutExtractionResult` carries `.text`, `.blocks`, `.column_count`, `.form_fields`, `.checkboxes`,
`.has_text_layer`, `.needs_ocr`, `.ocr_regions` and `.provenance`.

**Checkbox detection is deliberately narrow.** It fires on a ruled square of the right size, or on a
glyph known to be a box, and it requires two of the manual's own field labels to sit beside the box. On the
52 pages it fires zero times, and that is the correct answer: 10.4's approval form is a raster screenshot,
so the checkboxes exist as pixels and the layout extractor does not claim to have read them. A detector that
reported 13 checkboxes there would be reading the shape of the raster's compression artefacts. Reading the
checkboxes is OCR's job, not the layout extractor's, and it is the image *region* that carries the result.

### 2.3 `extractors/ocr.py` — system dependencies

From S3.3 (`origin/feat/Sprint-3-(S3.3)---Input-&-Output-Guardrails-&-Ocr`, Marcelino). Python deps are in
`requirements.txt` (`pytesseract==0.3.13`, `pillow`, `pdf2image==1.17.0`). Two things pip cannot install:

```bash
# 1. Tesseract, the OCR engine. pytesseract is only a binding; it shells out to this binary.
sudo apt-get install -y tesseract-ocr        # Debian/Ubuntu
brew install tesseract                      # macOS
choco install tesseract                     # Windows

# 2. Poppler, ONLY for the whole-file PDF path (scripts/check_ocr.py), because
#    pdf2image drives pdftoppm. The ingestion path does not need it -- it renders
#    regions with PyMuPDF and passes the image in.
sudo apt-get install -y poppler-utils       # Debian/Ubuntu
brew install poppler                        # macOS
```

Both are optional at runtime. `ingest_stressors` asks `ocr_available()` first and records the reason in the
payload when OCR is not usable, so a box without Tesseract indexes the same regions minus the words. Verify
with `python scripts/check_ocr.py --help` against a file you know the contents of.

---

## 3. Routing and stressor ingestion

`ingest_stressors.py` decides per page which extractors to run, from the page itself rather than from a
declared capability list — a section that declares a table may have the table on the next page, and
`find_tables` on a page of prose costs seconds and returns nothing.

```
page ──▶ find_tables("lines")?        ──▶ tables   reason: "N ruled grid(s)"
      ├─▶ has positioned text blocks?  ──▶ layout   reason: "positioned text blocks to order"
      ├─▶ no text layer?               ──▶ ocr      reason: "no text layer"
      └─▶ has images?                  ──▶ ocr      reason: "N image(s) with no text under them"
```

The routing also claims the **second page of a split table**. 7.1's journal resumes at the top of p26
under a repeated header, and p26 is the start of no section, so without this the rest of the journal would
be labelled under 7.2. `_continues_onto` fires when the previous page's last table reaches into the bottom
margin, the next page's first table starts in the top margin, and the two column counts differ by at most
one. The tolerance is one rather than zero because a split table is often re-detected narrower: 7.1's
spanned "Time to close" pair is merged on p25 and comes back as two plain columns on p26. Zero tolerance
would also let p27→p28 through, where a 3-column table at the foot of one page is followed by an unrelated
8-column one at the top of the next.

Routed pages, and what each one produced. One table point is indexed per table, and the p25→p26 journal
is a single point listing both pages — so it appears under p25 and is not counted again under p26:

| page | section | table points (nested) | layout | key-value (fields) | OCR regions |
|---|---|---|---|---|---|
| 8 | 2.1 | 2 | 1 | 1 (10) | — |
| 10 | 3.3 | 2 | 1 | 1 (8) | — |
| 15 | 5.2 | 6 (5) | 1 | 1 (10) | — |
| 25 | 7.1 | 2 | 1 | 1 (14) | 1 |
| 26 | 7.1 | 2 | 1 | 1 (14) | — |
| 29 | 8.3 | 2 | 1 | 1 (3) | — |
| 35 | 10.4 | — | 1 | — | 1 (raster form) |
| 41 | 11.7 | 6 (4) | 1 | 1 (6) | — |
| 42 | 11.8 | — | 1 | — | 2 |
| 44 | 12.2 | 1 | 1 | 1 (6) | 1 |
| 49 | Appendix C | 1 | 1 | — | — |
| | **total** | **24 (9 nested)** | **11** | **8 (71)** | **5** |

**The form extractor over-fires off 7.1.** It is tuned for an incident form and reports 71 fields across
these pages, of which 14 are 7.1's real form; the other 57 are table rows on 12.2, 11.7, 5.2, 3.3, 2.1 and
8.3 read as label-above-value pairs. That is a false positive, recorded here rather than smoothed over. It
does not corrupt retrieval — the layout point for the same page carries the prose and the table point
carries the grid — but a caller reading `form_fields` on 12.2 is reading table rows, not a form. Fixing it
means teaching the extractor that a page with a ruled grid is a table page; the honest place for that is
the router, which already knows, and it is left for the sprint that owns form extraction rather than
patched here behind a threshold.
48 points total, in `QDRANT.collection_name` alongside the KB articles, not in a collection of their own.
That is the whole point: a live query sees one corpus, and a no-regression check run against a separate
collection would pass whether or not the extractors were any good, because the baseline queries would never
touch a stressor point. `barq_manual` (the S2.4 line reading) is left as it was — it is an alternative
indexing of the same pages for comparison, not additional live content.

The entry point is **`ingest.py`**, alongside `ingest_articles()` and `sync_kb()`; `ingest_stressors.py`
holds the routing and extraction logic and is not called directly.

```python
from src.retrieval.ingest import ingest_stressors, drop_stressors
ingest_stressors()     # {"points": 48, "collection": "barq_knowledge_base", "shared_with_kb": True, ...}
drop_stressors()       # removes only is_stressor points; the KB articles are untouched
```

Two consequences of sharing a collection with `sync_kb()`, both handled and both tested:

- A stressor point carries an `article_id` (the section label) and no `content_hash`, which is exactly what
  an article that has disappeared from ServiceNow looks like. Left alone, the next `sync_kb()` would delete
  all 48. `_stored_hashes()` skips `is_stressor` points, and `_delete_article()` carries
  `must_not: is_stressor = true` so a section label colliding with an article id cannot take the other down.
- Collection creation stays with `_ensure_collection()`, so a manual point cannot be indexed against a
  different vector size, distance or embedding model than the KB articles were.

Every stressor point keeps the manual's own payload keys — `number`, `article_id`, `title`, `section`,
`section_id`, `section_label`, `category`, `service`, `workflow_state`, `version`, `security_level`,
`chunk_index`, `page_start`, `text`, `source` — so it is filterable by the same fields as a manual point.
Three keys are added alongside:

- `is_stressor: true`
- `capability_class` — `table`, `key_value`, `layout` or `ocr`, naming the **artifact** rather than the
  section's declared test; the same page is read two ways and both are worth a hit
- `extractor` — `tables`, `layout` or `ocr`
- `provenance` — per artifact: `has_merged_header`, `has_nested`, `nested_count`, `spans_page_boundary`,
  `columns`, `issues` for tables; `column_count`, `reading_order_changed`, `field_count`, `fields` for
  forms; `region_bbox`, `region_px`, `ocr_applied`, `reason`, and when OCR ran also `ocr_confidence`,
  `ocr_text_chars` and `tesseract_cmd_version` for images

---

## 4. Evaluation

`data/coverage_matrix.csv` is the S1.4 27-row incident matrix, unchanged byte for byte, plus 20 manual
stressor rows — 47 rows. It is generated by `tools/build_coverage_matrix.py` from
`data/coverage_matrix_s14_baseline.csv`, which is the file as it was at `d9b1be7^`. The generator asserts
the 27 rows round-trip unchanged, because an evaluation set that has drifted from the matrix it claims to
be built from is not a baseline, it is a second experiment wearing the first one's name.

The 20 stressor rows are graded on `expected_sections`, not `expected_articles`. The two axes are not
comparable: no KB number resolves to a manual section, and grading a stressor row against an empty expected
set would score every one of them as a miss no matter what came back. Two rows (`STR-19`, `STR-20`) are
intentionally unanswerable — the manual does not cover a sensor fleet and sets no retention period.

`eval/evaluation_set.json` v1.2 holds 57 rows: 27 coverage-matrix incidents, 10 identifier probes, 20
manual stressors. **The 37 v1.1 items are emitted byte-identically**, so a v1.2 run is compared against the
v1.1 fingerprint rather than against itself.

`python eval/ablation.py` without flags runs the KB half against the live corpus and skips the stressor
rows with a printed note. `--stressors` runs the manual half across the three arms. `--regression` scores
the 40 baseline rows twice — once with `is_stressor` filtered out, once unfiltered — in a single pass, so
the only variable is whether the manual's pages are eligible. `grade()` and `sparse_wins()` are untouched,
so `tests/test_ablation.py` still pins them.

An arm is a filter, not a collection:

| arm | filter | eligible |
|---|---|---|
| `baseline` | `is_stressor` absent | the KB articles |
| `stressor` | `is_stressor = true` | the manual's extracted pages |
| `combined` | none | everything |

`baseline` scores 0.000 on the stressor rows and that is the expected reading, not a defect: the KB articles
carry no manual section labels, so a section-level question can only be answered by a manual point. The
extractor-quality question — is the extractor reading better than the line reading — is a different
comparison against `barq_manual`, and is reported separately in the sprint report.

---

## 5. Known limits

- **Two accepted regressions, one pattern: `INC1027` and `INC1011`.** Both are rank displacements caused by
  mixed-corpus retrieval, not index corruption and not extraction defects. In each case a relevant manual
  or OCR point scores above the KB article that answers the query, takes its top-5 slot, and cannot itself
  satisfy the row because a manual section carries no KB number (`number: ""`). `dense` is unaffected
  (0.281 → 0.281); `hybrid` loses one query (0.312 → 0.281); `hybrid_rerank` loses two (0.312 → 0.250).

  `INC1027` — *"new starter locked out on day one and also cannot see the finance shared folder"*, expecting
  `KB0005`/`KB0020` — has been lost since the manual's pages joined the shared collection.

  `INC1011` — *"user locked out and also cannot connect VPN after password change this morning"*, expecting
  `KB0005`/`KB0001` — appeared once OCR text was indexed. The displacing point is the OCR region for 7.1's
  incident form (page 25, confidence 86.01), taken at rank 1 with score 5.67. **That read is correct** — its
  text describes a locked-out user, which is what the query is about — so the reranker is ranking a relevant
  document highly and the extractor is doing its job. Raising `MIN_OCR_CONFIDENCE` would not help: no
  threshold below 86.01 excludes it, and one above it discards a correct read along with 10.4's approval form.
  The issue is slot allocation, not OCR quality.

  **Accepted as-is, mentor-approved: no k change, no intent filter.** `k=5` is shared with S2.4/S2.5, and
  changing it this close to the deadline was assessed as more downstream risk than the two queries it would
  recover. The net trade is **2 distinct baseline queries lost** — (3 query-arm pairs: `INC1027` lost under `hybrid` and `hybrid_rerank`; `INC1011` lost under `hybrid_rerank` only) — against **18
  manual-section questions gained**, including 10.4's approval form becoming readable as text. See report §2.1–§2.2.

- **OCR is wired and measured against real Tesseract 5.3.4** — not projected, not mocked.
  `extractors/ocr.py` came from `origin/feat/Sprint-3-(S3.3)---Input-&-Output-Guardrails-&-Ocr` (S3.3,
  Marcelino) and `ingest_stressors.py` renders each image region with PyMuPDF and reads it. Two gates: OCR
  must be available (the `pytesseract` binding importable *and* a `tesseract` binary on PATH), and Tesseract's
  own confidence must be ≥ `MIN_OCR_CONFIDENCE` (55) before the text is indexed. A region that fails either
  gate is still indexed — with its bbox, pixel size, `ocr_applied` and a `reason` — so nothing is silently
  dropped. **5 regions across 4 pages (25, 35, 42, 44)**: 3 indexed at confidence 86.0 / 85.0 / 79.0, and 2
  correctly rejected by the gate at 22.3 / 30.7 — Tesseract returned text in both rejected cases and it was
  wrong enough not to be worth having. **All numbers in this document were measured with the OCR text
  indexed.** See §6.1 of the report.
- **`page_crossing` is declared on 7.1, not 8.3.** The contents page reads as though 8.3 is one long
  register; it is two, a problem register on p29 and a known-error register on p30, and the page that
  actually breaks is 7.1's journal. `STR-12` is the row that catches answering the known-error question
  from the problem register.
- **The extractors do not help where the heading already says the answer.** `merged_header`,
  `nested_table` and `ocr` score 1.000 in *both* arms: the line reading finds those sections because their
  headings survive line extraction. Those rows are regression guards, not wins.
- **Both arms answer both unanswerable rows.** Retrieval has no refusal; `answered_unanswerable` is
  reported rather than scored, and catching it is the answer layer's job.
- **p95 latency is one HTTP round-trip, not retrieval.** `embed_dense` posts a request to
  `LITELLM_BASE_URL` per query and measured 845–1317 ms, while the Qdrant searches it feeds took 4–20 ms
  each. Every latency number in either report is dominated by that call, and the 500 ms budget is exceeded
  with or without the extractors. The extractors are paid for once at index time, never per query.
- **`--regression` uses a filter, not a physical removal.** The filter is applied inside Qdrant before
  ranking, so `is_stressor=False` returns the same KB points in the same order a collection holding only KB
  points would. `python -m src.retrieval.ingest drop-stressors` is the physical check, and it is the
  rollback path as well.
- **`STRESSOR_SECTIONS` and `IMAGE_TRAITS` are separate vocabularies.** The first lists extraction outcomes
  a caller can rely on; the second says why a page's picture is hard, for whoever wires OCR in.
  `dark_theme`, `rotated_image` and `arabic_rtl` were declared as capabilities until a test caught that
  nothing produces them.

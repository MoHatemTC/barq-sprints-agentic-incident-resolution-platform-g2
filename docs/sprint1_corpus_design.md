# Sprint 1 — Knowledge Corpus Design (S1.4)

**Owner:** Abdullah Ashraf
**Status:** Working corpus (authored fallback, pending official supplied dataset), published to a dedicated ServiceNow Knowledge Base ("AI Incident Orchestrator KB")
**Path:** Path B-style schema/validation with authored fallback content — see §0

## 0. Data provenance

No official 25+ article JSON dataset was delivered during the sprint, so this corpus combines:

1. **10 real articles (KB0001–KB0010)** from the IT Service Desk Operations Manual, including the genuine near-duplicate pair (KB0010 v1, retired after a documented 40-minute outage — MIR-2026-03 — superseded by v2, published).
2. **17 additional records (KB0011–KB0026)** authored in the same style/depth to reach the 25+ minimum and cover categories/services absent from the original 10 (identity, video conferencing, onboarding, hardware peripherals) — including a second synthetic near-duplicate pair for KB0022.

If an official dataset is delivered later, this document's structure (schema, allowed values, chunking rule, mapping format) stays valid — only the article/incident content in §2–4 would be replaced and re-validated per §5.

## 1. Metadata schema

Matches the payload structure in `src/retrieval/ingest.py`:

| Field | Type | Allowed values | Notes |
|---|---|---|---|
| `article_number` | string | `KB00NN` | ServiceNow-style KB number; used as `article_id` internally |
| `title` | string | free text | |
| `body` | string | Markdown, `##`-delimited | Chunked by section (§6) |
| `category` | string | `network`, `software`, `hardware`, `inquiry` | |
| `service` | string | e.g. `corporate-vpn`, `corporate-email`, `identity`, `endpoint`, `print-services`, `file-services`, `sap-erp`, `corporate-wifi`, `order-processing`, `collaboration`, `onboarding` | |
| `workflow_state` | string | `draft`, `published`, `retired` | Governs whether an article can be surfaced as a valid answer |
| `version` | integer | 1, 2, 3... | Used in the point-ID scheme (§7) so multiple versions coexist in the index |
| `security_level` | string | `internal`, `confidential` | **Inferred** — source manual had no such field. Identity/MFA articles marked `confidential`; rest `internal`. Needs validation against an official dataset if one arrives. |

## 2. Article inventory (28 records / 26 unique KB numbers)

| article_number | title | category | service | workflow_state | version |
|---|---|---|---|---|---|
| KB0001 | VPN authentication fails after a password change | network | corporate-vpn | published | 2 |
| KB0002 | Outlook shows Disconnected and no mail delivered | software | corporate-email | published | 3 |
| KB0003 | Mapped shared drive missing after sign-in | network | file-services | published | 2 |
| KB0004 | Print jobs queue but nothing prints | hardware | print-services | published | 1 |
| KB0005 | Account locked after repeated failed sign-ins | inquiry | identity | published | 4 |
| KB0006 | MFA after lost/replaced device | inquiry | identity | published | 3 |
| KB0007 | Laptop performance degrades after update | hardware | endpoint | published | 2 |
| KB0008 | SAP GUI RFC_ERROR_COMMUNICATION | software | sap-erp | published | 1 |
| KB0009 | Wi-Fi drops on 5GHz network | network | corporate-wifi | published | 2 |
| KB0010 (v1) | Order service pool exhaustion | software | order-processing | **retired** | 1 |
| KB0010 (v2) | Order service pool exhaustion | software | order-processing | published | 2 |
| KB0011 | *(additional, draft)* | — | — | **draft** | 1 |
| KB0012 | *(additional, retired)* | — | — | **retired** | 1 |
| KB0013–KB0021 | *(additional, published)* | mixed | mixed | published | 1 |
| KB0022 (v1) | SAP GUI RFC_ERROR_COMMUNICATION legacy connection guide | software | sap-erp | **retired** | 1 |
| KB0022 (v2) | SAP GUI connection cleanup after APPSRV-OLD-04 migration | software | sap-erp | published | 2 |
| KB0023–KB0026 | *(additional, published)* | mixed | mixed | published | 1 |

*(Full content: `data/kb_dataset.json`.)*

## 3. Negative test cases (required for Sprint 4 evaluation)

| Type | Article(s) | Purpose |
|---|---|---|
| Near-duplicate version pair | KB0010 v1 (retired) + v2 (published) | Real documented incident (MIR-2026-03); tests correct-version filtering |
| Near-duplicate version pair | KB0022 v1 (retired) + v2 (published) | Outdated SAP app-server procedure superseded by current method |
| Draft | KB0011 | Unpublished content should not surface as a valid answer |
| Retired | KB0012, KB0022 v1 | Tests exclusion of superseded content |

## 4. Incident-to-article ground truth (27 incidents)

Ground truth: `data/coverage_matrix.csv`. Four incidents are real, worked examples from the manual's §7:

| incident_id | resolving_article_ids | is_answerable | requires_multi_doc | rationale |
|---|---|---|---|---|
| INC0010023 | KB0001 (v2) | true | false | Clean single-cause VPN auth failure |
| INC0010047 | *(none)* | **false** | false | Printer mechanical fault — KB0004 explicitly doesn't apply |
| INC0010064 | KB0005 (v4) | true | false | Three symptoms, one root cause |
| INC0010052 | KB0010 (v2) | true | false | P1, escalated; resolved via current article, not the retired v1 |

The remaining 23 incidents were authored to exercise the rest of the corpus: **5 multi-document** (`requires_multi_doc = true`, e.g. combined VPN + Wi-Fi issue) and **5 unanswerable** (`is_answerable = false`, e.g. procurement/non-IT requests) — used to confirm the system escalates rather than fabricates an answer.

The W0.3 incidents were not present in the shared PDI. Sarah Nader confirmed seeding W0.3 was outside S1.4's scope; Aya Ashraf, the internship admin, confirmed W0.3 would not be seeded and that generated incidents could be used for testing instead. The matrix is kept honest: the additional incidents are documented as generated validation records, not official W0.3 seed data.

## 5. Path B: validation and normalization

No official supplied JSON dataset was delivered during Sprint 1, so the corpus above is authored fallback content (§0), not a validated official dataset.

`scripts/validate_kb_dataset.py` validates any supplied dataset against the schema in §1 — checking missing/empty fields, duplicate `article_number`s (distinguishing accidental duplicates from intentional version pairs via version/workflow_state), inconsistent `category`/`service` values, missing `security_level`, and leftover HTML.

Run against the corpus in this repo:

```
Loaded 28 articles from data/kb_dataset.json
Articles missing required fields: 0
Articles with empty (but present) fields: 0
Duplicate article_numbers: 2 (KB0010, KB0022 — intentional version pairs)
Articles missing security_level specifically: 0
Articles containing raw HTML in body: 0
```

If an official dataset arrives later: run the validator against it, normalize any flagged issues, confirm the negative-test cases (§3) are present or author the missing ones by hand, then replace `data/kb_dataset.json` and re-run `ingest.py` — no pipeline code changes needed.

## 6. Chunking rule

`src/retrieval/chunking.py` splits on `##` Markdown headers — each section becomes one chunk, so numbered procedures never split mid-step. The section label is stored in each point's payload as `section`, so results can be attributed to a specific part of the source article.

## 7. Point ID scheme (idempotency + version coexistence)

Point IDs are deterministic: `sha256(article_id:version:chunk_index)`, formatted as a UUID.

- Re-running ingestion on unchanged content produces the same IDs (verified idempotent — `docs/sprint1_index_spec.md`).
- Different versions of the same article coexist as distinct points (verified: KB0010 and KB0022 both exist as v1/v2 pairs independently in the index), letting later retrieval-filtering logic be tested against a real "old vs. current" scenario.

## 8. Known gaps

- `security_level` was inferred, not sourced — needs validation against an official dataset if one arrives.
- This corpus is an authored fallback; if a real dataset is delivered, it must go through §5's workflow and this document updated accordingly.
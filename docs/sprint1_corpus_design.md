# Sprint 1 — Knowledge Corpus Design (S1.4)

**Owner:** Abdullah Ashraf
**Status:** Working corpus, authored fallback pending official supplied dataset
**Path:** Path B-style schema/validation with authored fallback content — see note below

## 0. Important note on data provenance

This corpus was built from two sources, combined because the official 25+ article
JSON dataset referenced in the sprint plan had not yet been delivered by the time
this design and the pipeline needed to be proven end-to-end:

1. **10 real articles (KB0001–KB0010)** extracted from the IT Service Desk
   Operations Manual shared by the team, including the genuine documented
   near-duplicate version pair (KB0010 v1, retired after a documented 40-minute
   outage — MIR-2026-03 — superseded by v2, published).
2. **17 additional records (KB0011–KB0026)** authored in the same domain,
   style and technical depth to bring the corpus to the required 25+ minimum,
   and to cover categories/services not represented in the original 10
   (identity requests, video conferencing, onboarding, hardware peripherals).
   This includes the synthetic near-duplicate SAP migration pair for KB0022.

If/when the official supplied dataset lands, this document's **structure**
(schema, allowed values, chunking rule, mapping rationale format) remains
valid — only the article and incident content in Sections 2–4 would be
replaced, validated and normalized per the Path B workflow
(`scripts/validate_kb_dataset.py`).

## 1. Metadata schema

Every article carries the following fields, matching the payload structure
in `src/retrieval/ingest.py`:

| Field | Type | Allowed values | Notes |
|---|---|---|---|
| `article_number` | string | `KB00NN` format | ServiceNow-style KB number; used as `article_id` internally |
| `title` | string | free text | Article title |
| `body` | string | Markdown, `##`-delimited sections | Chunked by section (see §5) |
| `category` | string | `network`, `software`, `hardware`, `inquiry` | High-level classification |
| `service` | string | e.g. `corporate-vpn`, `corporate-email`, `identity`, `endpoint`, `print-services`, `file-services`, `sap-erp`, `corporate-wifi`, `order-processing`, `collaboration`, `onboarding` | Maps to the affected system/service |
| `workflow_state` | string | `draft`, `published`, `retired` | Governs whether an article should be surfaced as a valid answer |
| `version` | integer | `1, 2, 3, ...` | Incremented on content revision; used in the point-ID scheme (§5) so multiple versions of the same article coexist in the index |
| `security_level` | string | `internal`, `confidential` | **Inferred** — the source manual did not include this field explicitly. Identity/MFA-related articles were marked `confidential`; all others `internal`. This inference must be validated against the official dataset if/when it supersedes this corpus. |

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

*(Full field-by-field content lives in `data/kb_dataset.json`.)*

## 3. Negative test cases (required for Sprint 4 evaluation)

| Type | Article(s) | Purpose |
|---|---|---|
| Near-duplicate version pair | KB0010 v1 (retired) + KB0010 v2 (published) | Tests that retrieval/filtering correctly surfaces the current version and excludes the retired one — a real documented incident (MIR-2026-03) rather than synthetic |
| Near-duplicate version pair | KB0022 v1 (retired) + KB0022 v2 (published) | Tests that an outdated SAP application-server procedure for APPSRV-OLD-04 is superseded by the current message-server cleanup path |
| Draft | KB0011 | Tests that unpublished content is not surfaced as a valid answer |
| Retired | KB0012, KB0022 v1 | Tests exclusion of superseded content |

## 4. Incident-to-article ground truth (27 incidents)

Ground truth lives in `data/coverage_matrix.csv`. Four incidents are the
**real, worked examples** from the manual's Section 7:

| incident_id | resolving_article_ids | is_answerable | requires_multi_doc | rationale |
|---|---|---|---|---|
| INC0010023 | KB0001 (v2) | true | false | Clean single-cause VPN auth failure, matches article directly |
| INC0010047 | *(none)* | **false** | false | Printer mechanical fault — KB0004 explicitly does not apply; deliberately unanswerable |
| INC0010064 | KB0005 (v4) | true | false | Three reported symptoms, one root cause (account lockout) |
| INC0010052 | KB0010 (v2) | true | false | P1, high-risk, escalated; resolved via the *current* order-service article, not the retired v1 |

The remaining 23 incidents were authored to exercise the rest of the corpus,
including:
- **5 multi-document incidents** (`requires_multi_doc = true`) — symptoms
  that require combining two articles to resolve (e.g. a VPN + Wi-Fi
  combined connectivity issue).
- **5 unanswerable incidents** (`is_answerable = false`) — issues genuinely
  outside the KB's scope (e.g. hardware procurement requests, non-IT
  administrative requests), used to test that the system escalates rather
  than fabricates an answer.

The W0.3 incidents were not present in the shared ServiceNow PDI. After the
internship admin confirmed they would not be seeded and that generated
incidents could be used for testing, the matrix was kept honest by treating
the additional incidents as generated validation records rather than official
W0.3 seed data.

## 5. Chunking rule

Articles are chunked by their `##`-level Markdown section headers
(`src/retrieval/chunking.py`). Each section (e.g. "Symptom", "Resolution",
"Related error codes") becomes exactly one chunk — numbered procedures are
never split mid-step. The section label is preserved and stored in each
point's payload as `section`, so retrieved results can be attributed back
to a specific part of the source article.

## 6. Point ID scheme (idempotency + version coexistence)

Point IDs are deterministic, derived from `sha256(article_id:version:chunk_index)`,
formatted as a UUID. This means:
- Re-running ingestion on unchanged content produces the same IDs (verified
  idempotent — see `docs/sprint1_index_spec.md`).
- **Different versions of the same article coexist** as distinct points
  (verified: KB0010 v1 and v2 both exist independently in the index),
  which is what allows retrieval-filtering logic in later sprints to be
  tested against a real "old vs. current" scenario rather than assuming
  only one version ever exists.

## 7. Known gaps / open items

- `security_level` was inferred, not sourced — needs validation against
  the official dataset when it arrives.
- This corpus is an authored fallback. If the official 25+ article JSON
  dataset is delivered, it must go through `scripts/validate_kb_dataset.py`
  and this document updated accordingly.

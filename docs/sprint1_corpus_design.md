# Sprint 1 Knowledge Corpus Design

The tracked source corpus is `data/kb_dataset.json`. It contains 28 records:
24 published, one draft, and three retired records. It is authored fallback
content, not a ServiceNow export.

## Schema

Every article has `article_number`, `title`, `body`, `category`, `service`,
`workflow_state`, `version`, and `security_level`. Categories are `software`,
`hardware`, `identity`, and `network`; security levels are `internal` and
`confidential`.

`article_number` is the stable local article identifier. `version` is part of
the deterministic Qdrant point ID. Retired records are excluded before
publishing and indexing. For duplicate article numbers, the loader selects the
highest non-retired version.

KB0010 and KB0022 are intentional retired-v1/published-v2 fixtures. KB0011 is
intentionally draft. Coverage expectations live in `data/coverage_matrix.csv`.

## Validation

```bash
python scripts/validate_kb_dataset.py data/kb_dataset.json
pytest tests/test_corpus_quality.py -q
```

The validator reports schema gaps, duplicates, raw HTML, and near-duplicate
version pairs. The known duplicate pairs are expected fixtures, not failures.

## Publishing and indexing

The local source loader applies version and retirement selection before either
the publisher or ingestion receives articles. `publish_kb.py` maps a category
label to a configured ServiceNow category sys_id and omits unmapped categories.
ServiceNow metadata columns are configured through
`SERVICENOW_KB_METADATA_FIELD_MAP`; no custom field name is hard-coded.

Bodies are converted from HTML when needed, split on `##` headings, and split
further only when a section exceeds the configured chunk size.

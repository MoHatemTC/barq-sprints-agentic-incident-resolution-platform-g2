# Sprint 1 Vector Index Specification

## Collection

`src.retrieval.ingest` writes the `barq_knowledge_base` Qdrant collection. It
stores a 384-dimensional cosine dense vector (`BAAI/bge-small-en-v1.5`) and a
named sparse BM25 vector (`Qdrant/bm25`) for each chunk. A marker point stores
the model fingerprint; ingestion stops before upsert if configured models differ.

Qdrant runs through `docker-compose.yml` and persists through `qdrant_data`.
PostgreSQL and Redis are infrastructure for later work.

## Chunks and point IDs

`chunk_article()` preprocesses HTML and preserves `##` section boundaries.
Oversized sections use `CHUNK_SIZE` and `CHUNK_OVERLAP` from configuration.
Point IDs are UUID-formatted SHA-256 hashes of:

```text
article_id:version:chunk_index
```

The version avoids KB0010/KB0022 revision collisions; unchanged source data
produces the same IDs on repeat ingestion.

## Payload

Each point stores `sys_id`, `number`, `article_id`, `title`, `section`,
`category`, `service`, `workflow_state`, `version`, `security_level`,
`chunk_index`, and chunk `text` for filtering and attribution.

## Sources and verification

Local JSON ingestion is the runnable path. The ServiceNow source provides a
pure `article_from_servicenow()` mapper for configured metadata fields, but the
ingestion command is not yet wired to fetch ServiceNow records.

```bash
docker compose up -d
python -m src.retrieval.ingest
python -m src.retrieval.smoke_test
```

A second ingestion run should keep the same point count. Sparse vectors are
stored for future hybrid retrieval. Ingestion upserts but does not delete
orphaned points when source content is removed; recreate the collection when
that cleanup is required.

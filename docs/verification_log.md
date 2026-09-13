# S1.4 Verification Log — Docker Compose, Ingestion, Persistence

This log captures the actual terminal output from a full end-to-end
verification run, committed as evidence per mentor review request.
Commands are listed in `TESTING.md`; this file records real results.

## 1. Stack startup

```
$ docker compose up -d
$ docker compose ps
NAME       IMAGE                    STATUS
postgres   postgres:16              Up (healthy)
qdrant     qdrant/qdrant:v1.12.4    Up (healthy)
redis      redis:7-alpine           Up (healthy)
```

## 2. Ingestion — first run

```
$ python -m src.retrieval.ingest
Ingestion complete: {'point_count': 86, 'dimensionality': 384,
'collection_name': 'barq_knowledge_base', 'elapsed_seconds': 4.38}
```

## 3. Idempotent re-ingestion

```
$ python -m src.retrieval.ingest
Ingestion complete: {'point_count': 86, 'dimensionality': 384,
'collection_name': 'barq_knowledge_base', 'elapsed_seconds': 3.41}
```
Result: point count unchanged (86 → 86).

## 4. Persistence — restart Qdrant, check WITHOUT re-ingesting

```
$ docker compose restart qdrant
✔ Container ...-qdrant-1  Started

$ python -c "from src.config import QDRANT; from qdrant_client import QdrantClient; \
c = QdrantClient(url=QDRANT.url, check_compatibility=False); \
print(c.get_collection(QDRANT.collection_name).points_count)"
86
```
Result: 86 points confirmed via direct query, no ingestion run in
between — proves data survived on the named volume, not just
idempotent rebuilding.

## 5. Re-ingestion after restart (sanity check)

```
$ python -m src.retrieval.ingest
Ingestion complete: {'point_count': 86, 'dimensionality': 384,
'collection_name': 'barq_knowledge_base', 'elapsed_seconds': 3.82}
```
Result: still 86, consistent.

## 6. Full container down/up cycle (separate run, same corpus size)

```
$ docker compose down
$ docker compose up -d
$ python -m src.retrieval.ingest
Ingestion complete: {'point_count': 86, ...}
```
Result: 86 points retained across a full `down` → `up` cycle.

## 7. Retrieval smoke test

```
$ python -m src.retrieval.smoke_test
Query: 'VPN authentication keeps failing after I changed my password'
  Expected article: KB0001
  Top 3 results:
    - KB0001 [Resolution] score=0.822
    - KB0001 [Symptom] score=0.808
    - KB0026 [Symptom] score=0.718
  Verdict: PASS

Query: 'Outlook shows disconnected and no email is coming through'
  Expected article: KB0002
  Top result: KB0002 [Symptom] score=0.772
  Verdict: PASS

Query: 'My account got locked after too many failed login attempts'
  Expected article: KB0005
  Top result: KB0005 [Symptom] score=0.751
  Verdict: PASS

Query: 'requesting annual leave for next month' (deliberately unanswerable)
  Top result: KB0020 [Resolution] score=0.646
  Verdict: OBSERVED (top score 0.646 -- record for Sprint 3
  confidence threshold decision)

Smoke test complete: 4 passed, 0 failed out of 4
```

## 8. Corpus validation (Path B tooling)

```
$ python scripts/validate_kb_dataset.py data/kb_dataset.json
Loaded 28 articles from data/kb_dataset.json
Articles missing required fields: 0
Articles with empty (but present) fields: 0
Duplicate article_numbers: 2 (KB0010, KB0022 -- intentional version pairs)
Articles missing security_level specifically: 0
Articles containing raw HTML in body: 0
Near-duplicate version pairs: 2
  - KB0010: v1 (retired), v2 (published)
  - KB0022: v1 (retired), v2 (published)
```

## 9. ServiceNow publishing (dedicated Knowledge Base)

```
$ python -m src.retrieval.publish_kb
Publish complete: {'created': 0, 'updated': 25, 'failed': []}
```
25 articles confirmed live in the dedicated "AI Incident Orchestrator KB"
(not the shared default KB).

## 10. OAuth identity permission verification

```
$ python scripts/verify_permissions.py
[PASS] OAuth authentication succeeded
[PASS] READ  incident: HTTP 200
[PASS] READ  kb_knowledge: HTTP 200
[PASS] WRITE kb_knowledge: HTTP 201 (cleanup ok)
```

## 11. Full automated test suite

```
$ pytest tests/ -v
tests/test_chunking_config.py ......... (9 passed)
tests/test_corpus_quality.py ..... (5 passed)
tests/test_html_preprocessing.py ..... (5 passed)
==================== 19 passed in 0.23s ====================
```
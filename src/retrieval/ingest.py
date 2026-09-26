"""
Orchestrates: get articles (from a pluggable source) -> dedupe -> chunk ->
embed (dense + sparse) -> upsert to Qdrant. Deterministic point IDs make
re-running this idempotent.
"""

import time
import hashlib
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, PointStruct, SparseVector,
    Filter, FieldCondition, MatchValue, FilterSelector
)

from ..config import QDRANT
from .embedding import embed_dense, embed_sparse, get_model_fingerprint, get_dense_dimension
from .chunking import chunk_article
from .schema import Article


# Qdrant point IDs must be unsigned integers or UUIDs.
MODEL_FINGERPRINT_MARKER_ID = "00000000-0000-0000-0000-000000000001"


def deterministic_point_id(article_id: str, version: int, chunk_index: int) -> str:
    raw = f"{article_id}:{version}:{chunk_index}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _ensure_collection(client: QdrantClient):
    dense_dim = get_dense_dimension()
    fingerprint = get_model_fingerprint()

    if client.collection_exists(QDRANT.collection_name):
        marker = client.retrieve(QDRANT.collection_name, ids=[MODEL_FINGERPRINT_MARKER_ID])
        if marker and marker[0].payload.get("fingerprint") != fingerprint:
            raise RuntimeError(
                f"Embedding model mismatch: collection was built with "
                f"'{marker[0].payload.get('fingerprint')}' but current config is "
                f"'{fingerprint}'. Aborting to avoid a corrupted index."
            )
        return

    client.create_collection(
        collection_name=QDRANT.collection_name,
        vectors_config={"dense": VectorParams(size=dense_dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )
    client.upsert(QDRANT.collection_name, points=[
        PointStruct(
            id=MODEL_FINGERPRINT_MARKER_ID,
            vector={"dense": [0.0] * dense_dim},
            payload={"fingerprint": fingerprint, "_is_marker": True},
        )
    ])


def _build_points(articles: list[Article]) -> list[PointStruct]:
    points = []
    for article in articles:
        chunks = chunk_article(article.body)
        for i, (section, chunk_text) in enumerate(chunks):
            points.append(PointStruct(
                id=deterministic_point_id(article.article_id, article.version, i),
                vector={
                    "dense": embed_dense(chunk_text),
                    "sparse": SparseVector(**embed_sparse(chunk_text)),
                },
                payload={
                    "sys_id": article.sys_id,
                    "number": article.number,
                    "article_id": article.article_id,
                    "title": article.title,
                    "section": section,
                    "category": article.category,
                    "service": article.service,
                    "workflow_state": article.workflow_state,
                    "version": article.version,
                    "security_level": article.security_level,
                    "chunk_index": i,
                    "text": chunk_text,
                },
            ))
    return points


def ingest_articles(source: str = "servicenow") -> dict:
    """
    source: "servicenow" (published kb_knowledge records via S1.5's client).
    The BARQ manual PDF is indexed separately by ingest_manual.py.
    """
    start = time.time()
    client = QdrantClient(url=QDRANT.url, check_compatibility=False)
    _ensure_collection(client)

    if source == "servicenow":
        from .sources.servicenow_source import load_articles_from_servicenow
        articles = load_articles_from_servicenow()
        if not articles:
            raise RuntimeError("ServiceNow returned 0 published articles; aborting sync to avoid wiping Qdrant.")
    else:
        raise ValueError(f"Unknown source: {source}")

    points = _build_points(articles)
    client.upsert(collection_name=QDRANT.collection_name, points=points)

    elapsed = time.time() - start
    info = client.get_collection(QDRANT.collection_name)
    stats = {
        "point_count": info.points_count,
        "dimensionality": get_dense_dimension(),
        "collection_name": QDRANT.collection_name,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(f"Ingestion complete: {stats}")
    return stats
def _content_hash(article: Article) -> str:
    raw = f"{article.title}|{article.category}|{article.service}|{article.security_level}|{article.body}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _stored_hashes(client: QdrantClient) -> dict[str, str]:
    """article_id -> content_hash for every KB article currently in Qdrant.

    Stressor points (S2.6) live in this collection too and carry an article_id,
    but they are not ServiceNow articles and have no content_hash.  They are
    skipped: without this, sync_kb would see them as articles that have
    disappeared from ServiceNow and delete every one of them on its next run.
    """
    stored, offset = {}, None
    while True:
        points, offset = client.scroll(
            QDRANT.collection_name,
            limit=256,
            offset=offset,
            with_payload=["article_id", "content_hash", "_is_marker", "is_stressor"],
            with_vectors=False,
        )
        for p in points:
            if p.payload.get("_is_marker") or p.payload.get("is_stressor"):
                continue
            stored[p.payload.get("article_id")] = p.payload.get("content_hash", "")
        if offset is None:
            return stored


def _delete_article(client: QdrantClient, article_id: str):
    """Drop every chunk of one KB article, and only KB articles.

    The must_not keeps a stressor point alive if a manual section ever carries an
    article_id equal to a KB article's: the two are indexed from different sources
    and one must not be able to delete the other.
    """
    client.delete(
        QDRANT.collection_name,
        points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="article_id", match=MatchValue(value=article_id))],
            must_not=[FieldCondition(key="is_stressor", match=MatchValue(value=True))],
        )),
    )


def sync_kb() -> dict:
    """
    Manual KB sync from ServiceNow. Only touches articles that changed.
    Returns {"status", "added", "updated", "deleted", "unchanged"}.
    """
    start = time.time()
    client = QdrantClient(url=QDRANT.url, check_compatibility=False)
    _ensure_collection(client)

    from .sources.servicenow_source import load_articles_from_servicenow
    articles = load_articles_from_servicenow()
    current = {a.article_id: a for a in articles}
    stored = _stored_hashes(client)

    added = updated = unchanged = 0
    for article_id, article in current.items():
        new_hash = _content_hash(article)
        if article_id not in stored:
            added += 1
        elif stored[article_id] != new_hash:
            updated += 1
            _delete_article(client, article_id)   # drop old chunks
        else:
            unchanged += 1
            continue                              # nothing to embed

        points = _build_points([article])
        for p in points:
            p.payload["content_hash"] = new_hash
        if points:
            client.upsert(QDRANT.collection_name, points=points)

    # articles that disappeared from ServiceNow / are no longer published
    removed = [a for a in stored if a not in current]
    for article_id in removed:
        _delete_article(client, article_id)

    result = {
        "status": "up_to_date" if not (added or updated or removed) else "synced",
        "added": added,
        "updated": updated,
        "deleted": len(removed),
        "unchanged": unchanged,
        "elapsed_seconds": round(time.time() - start, 2),
    }
    print(f"KB sync complete: {result}")
    return result


def ingest_stressors(source: str = "manual", client: QdrantClient | None = None) -> dict:
    """
    S2.6: index the BARQ manual's extracted pages into the same collection as the
    KB articles.

    This is the entry point for stressor ingestion, alongside ingest_articles()
    and sync_kb().  The manual is read twice: once as a column of lines by
    ingest_manual.py, and once here through the table, form and layout
    extractors.  The second reading goes into QDRANT.collection_name rather than a
    collection of its own, because a live query sees one corpus -- a manual
    section and a KB article compete for the same top-k slots, and a no-regression
    check against a separate collection would pass whether or not the extractors
    were any good.

    source: "manual" (the only value today; kept for symmetry with
    ingest_articles so a second stressor source can be added without changing the
    signature).

    Returns the per-artifact counts from the routing pass, the number of points
    written, and the shared collection's new total.
    """
    if source != "manual":
        raise ValueError(f"Unknown stressor source: {source}")

    from .ingest_stressors import build_stressor_points, count_stressor_points, upsert_stressor_points

    start = time.time()
    client = client or QdrantClient(url=QDRANT.url, check_compatibility=False)

    points, stats = build_stressor_points()
    stats.update(upsert_stressor_points(client, points))
    stats["stressor_points_in_collection"] = count_stressor_points(client)
    stats["elapsed_seconds"] = round(time.time() - start, 2)
    print(f"Stressor ingestion complete: {stats}")
    return stats


def drop_stressors(client: QdrantClient | None = None) -> dict:
    """
    S2.6: remove the manual's extracted pages from the shared collection, leaving
    the KB articles in place.

    The rollback for a bad extraction run, and half of the no-regression check:
    score the baseline queries with the manual mixed in, drop it, score them
    again, and compare.  It is not sync_kb()'s job -- sync_kb reconciles against
    ServiceNow and does not know the manual exists.
    """
    from .ingest_stressors import drop_stressor_points

    client = client or QdrantClient(url=QDRANT.url, check_compatibility=False)
    dropped = drop_stressor_points(client)
    result = {"dropped": dropped, "collection": QDRANT.collection_name}
    print(f"Stressor points dropped: {result}")
    return result


if __name__ == "__main__":
    import sys

    verb = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if verb == "sync":
        sync_kb()
    elif verb == "stressors":
        ingest_stressors()
    elif verb == "drop-stressors":
        drop_stressors()
    else:
        ingest_articles(source="servicenow")

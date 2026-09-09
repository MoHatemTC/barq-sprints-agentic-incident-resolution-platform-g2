"""
Orchestrates: get articles (from a pluggable source) -> chunk -> embed
(dense + sparse) -> upsert to Qdrant. Deterministic point IDs make
re-running this idempotent.
"""

import os
import time
import hashlib
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, PointStruct, SparseVector
)

from .embedding import embed_dense, embed_sparse, get_model_fingerprint, get_dense_dimension
from .chunking import chunk_article
from .schema import Article
from .sources.local_json_source import load_articles_from_json

load_dotenv()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "barq_knowledge_base")

# Qdrant point IDs must be an unsigned integer or a UUID -- not an arbitrary string.
MODEL_FINGERPRINT_MARKER_ID = "00000000-0000-0000-0000-000000000001"


def deterministic_point_id(article_id: str, chunk_index: int) -> str:
    raw = f"{article_id}:{chunk_index}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _ensure_collection(client: QdrantClient):
    dense_dim = get_dense_dimension()
    fingerprint = get_model_fingerprint()

    if client.collection_exists(COLLECTION_NAME):
        marker = client.retrieve(COLLECTION_NAME, ids=[MODEL_FINGERPRINT_MARKER_ID])
        if marker and marker[0].payload.get("fingerprint") != fingerprint:
            raise RuntimeError(
                f"Embedding model mismatch: collection was built with "
                f"'{marker[0].payload.get('fingerprint')}' but current config is "
                f"'{fingerprint}'. Aborting to avoid a corrupted index."
            )
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={"dense": VectorParams(size=dense_dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )
    client.upsert(COLLECTION_NAME, points=[
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
                id=deterministic_point_id(article.article_id, i),
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


def ingest_articles(source: str = "local", json_path: str = "data/kb_dataset.json") -> dict:
    """
    source: "local" (test path, reads json_path) or "servicenow" (final path,
    reads from ServiceNow via S1.5's client -- not yet wired up).
    """
    start = time.time()
    client = QdrantClient(url=QDRANT_URL, check_compatibility=False)
    _ensure_collection(client)

    if source == "local":
        articles = load_articles_from_json(json_path)
    elif source == "servicenow":
        from .sources.servicenow_source import load_articles_from_servicenow
        articles = load_articles_from_servicenow()
    else:
        raise ValueError(f"Unknown source: {source}")

    points = _build_points(articles)
    client.upsert(collection_name=COLLECTION_NAME, points=points)

    elapsed = time.time() - start
    info = client.get_collection(COLLECTION_NAME)
    stats = {
        "point_count": info.points_count,
        "dimensionality": get_dense_dimension(),
        "collection_name": COLLECTION_NAME,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(f"Ingestion complete: {stats}")
    return stats


if __name__ == "__main__":
    ingest_articles(source="local")
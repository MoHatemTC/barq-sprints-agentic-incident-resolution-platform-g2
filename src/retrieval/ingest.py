"""
Orchestrates: parse articles -> chunk -> embed (dense + sparse) -> upsert to Qdrant.
Deterministic point IDs make re-running this idempotent.
"""

import os
import time
import hashlib
import frontmatter
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, PointStruct, SparseVector
)

from .embedding import embed_dense, embed_sparse, get_model_fingerprint, get_dense_dimension
from .chunking import chunk_article

load_dotenv()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "kb_articles")

# Qdrant point IDs must be an unsigned integer or a UUID -- not an arbitrary string.
MODEL_FINGERPRINT_MARKER_ID = "00000000-0000-0000-0000-000000000001"


def deterministic_point_id(article_id: str, chunk_index: int) -> str:
    raw = f"{article_id}:{chunk_index}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def load_articles(articles_dir: str) -> list[dict]:
    articles = []
    for path in Path(articles_dir).glob("*.md"):
        post = frontmatter.load(path)
        articles.append({**post.metadata, "content": post.content})
    return articles


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


def ingest_articles(articles_dir: str = "data/articles") -> dict:
    start = time.time()
    client = QdrantClient(url=QDRANT_URL, check_compatibility=False)
    _ensure_collection(client)

    articles = load_articles(articles_dir)
    points = []
    for article in articles:
        chunks = chunk_article(article["content"])
        for i, chunk_text in enumerate(chunks):
            points.append(PointStruct(
                id=deterministic_point_id(article["article_id"], i),
                vector={
                    "dense": embed_dense(chunk_text),
                    "sparse": SparseVector(**embed_sparse(chunk_text)),
                },
                payload={
                    "article_id": article["article_id"],
                    "title": article["title"],
                    "category": article["category"],
                    "service": article["service"],
                    "workflow_state": article["workflow_state"],
                    "version": article["version"],
                    "security_level": article["security_level"],
                    "chunk_index": i,
                    "text": chunk_text,
                },
            ))

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
    ingest_articles()
"""
S2.4 Evalution matrix : index the parsed manual into its own Qdrant collection (MANUAL_COLLECTION_NAME)
Mirror of S1.4's ingest.py: same vectors, same payload keys to run it : python -m src.retrieval.ingest_manual
"""

from qdrant_client import QdrantClient, models

from ..config import CHUNKING, RETRIEVAL
from .chunking import _split_with_overlap
from .embedding import embed_dense, embed_sparse, get_dense_dimension
from .hybrid_search import get_client
from .ingest import deterministic_point_id
from .manual_parser import ManualSection, parse_manual


def build_points(sections: list[ManualSection]) -> list[models.PointStruct]:
    points = []
    for s in sections:
        # Heading prefixed into every chunk so BM25 can match a section by its name.
        for i, chunk in enumerate(_split_with_overlap(s.text, CHUNKING.chunk_size, CHUNKING.chunk_overlap)):
            text = f"{s.section_label} {s.title}\n{chunk}"
            points.append(models.PointStruct(
                id=deterministic_point_id("manual:" + s.section_label, s.version, i),
                vector={"dense": embed_dense(text), "sparse": models.SparseVector(**embed_sparse(text))},
                payload={
                    "number": s.kb_number, "article_id": s.section_label, "title": s.title,
                    "section": s.section_id, "section_id": s.section_id, "section_label": s.section_label,
                    "category": s.category, "service": s.service,
                    "workflow_state": s.workflow_state, "version": s.version, "security_level": s.security_level,
                    "chunk_index": i, "page_start": s.page_start, "text": text, "source": "manual",
                },
            ))
    return points


def ingest_manual(client: QdrantClient | None = None) -> dict:
    client = client or get_client()
    name = RETRIEVAL.manual_collection_name
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config={"dense": models.VectorParams(size=get_dense_dimension(), distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )
    sections = parse_manual()
    points = build_points(sections)
    client.upsert(name, points)
    stats = {"collection": name, "sections": len(sections), "points": client.count(name, exact=True).count}
    print("Manual ingestion complete:", stats)
    return stats


if __name__ == "__main__":
    ingest_manual()

"""
this file for Query side of retrieval (S2.4). Three modes, chosen by RETRIEVAL_MODE:
 dense         : dense vector search only            (baseline)
  hybrid        : dense + sparse, fused with RRF
  hybrid_rerank : hybrid, then a cross-encoder re-scores the candidates
The metadata filter from filters.py is passed INTO every Qdrant search, so excluded chunks are never candidates
"""
import argparse
from dataclasses import dataclass

from qdrant_client import QdrantClient, models
from qdrant_client.local.qdrant_local import QdrantLocal

from ..config import QDRANT, RETRIEVAL
from .embedding import embed_dense, embed_sparse
from .filters import RetrievalFilters, build_qdrant_filter


@dataclass
class RetrievedChunk:
    point_id: str
    number: str      # KB article number, e.g. "KB0001"
    section: str     # chunk section, e.g. "Resolution"
    text: str
    score: float
    payload: dict    # full payload, for filtering / debugging / the report


def get_client() -> QdrantClient:
    # we use Docker Qdrant by default, embedded on-disk Qdrant is used if QDRANT_LOCAL_PATH is set
    if RETRIEVAL.qdrant_local_path:
        return QdrantClient(path=RETRIEVAL.qdrant_local_path)
    return QdrantClient(url=QDRANT.url, check_compatibility=False)


def _search_params(client: QdrantClient):
    # exact=True -> brute-force instead of approximate search, The corpus is tiny, so it costs
    # nothing and makes rankings reproducible. In-memory / on-disk Qdrant is always exact and warns if we pass params, so only send them to a real server.
    if isinstance(client._client, QdrantLocal):
        return None
    return models.SearchParams(exact=True)


def _to_chunks(points) -> list[RetrievedChunk]:
    # Qdrant ScoredPoint -> our RetrievedChunk
    return [
        RetrievedChunk(
            point_id=str(p.id),
            number=p.payload.get("number", ""),
            section=p.payload.get("section", ""),
            text=p.payload.get("text", ""),
            score=float(p.score),
            payload=p.payload,
        )
        for p in points
    ]


def _search_one(client, collection, vector_name, vector, qfilter, limit) -> list[RetrievedChunk]:
    # One filtered Qdrant search on a single named vector ("dense" or "sparse")
    result = client.query_points(
        collection_name=collection,
        query=vector,
        using=vector_name,
        query_filter=qfilter,      # <- pre-filter: applied while searching
        limit=limit,
        with_payload=True,
        search_params=_search_params(client),
    )
    return _to_chunks(result.points)


def rrf_fuse(lists: list[list[RetrievedChunk]], k: int) -> list[RetrievedChunk]:
    """ Reciprocal Rank Fusion: score(doc) = sum over lists of 1 / (k + rank)
    Only ranks are used, so cosine (dense) and BM25 (sparse) scores never have
    to be compare ,  A chunk found by both lists gets two terms and floats up """
    
    fused: dict[str, RetrievedChunk] = {}
    for ranked in lists:
        for rank, chunk in enumerate(ranked, start=1):
            if chunk.point_id not in fused:
                fused[chunk.point_id] = RetrievedChunk(**{**chunk.__dict__, "score": 0.0})
            fused[chunk.point_id].score += 1.0 / (k + rank)
    # Sort by score high -> low, on a tie, smaller point_id first (deterministic).
    return sorted(fused.values(), key=lambda c: (-c.score, c.point_id))


def search(
    query: str,
    mode: str | None = None,
    top_k: int | None = None,
    filters: RetrievalFilters | None = None,
    client: QdrantClient | None = None,
    collection: str | None = None,
) -> list[RetrievedChunk]:
    """Run one query in the given mode, Every argument defaults to config"""
    mode = mode or RETRIEVAL.mode
    top_k = top_k or RETRIEVAL.top_k
    client = client or get_client()
    collection = collection or QDRANT.collection_name
    qfilter = build_qdrant_filter(filters)

    if mode == "dense":
        return _search_one(client, collection, "dense", embed_dense(query), qfilter, top_k)

    # hybrid and hybrid_rerank both start the same way: two filtered searches, fused.
    k = RETRIEVAL.candidate_k
    dense_hits = _search_one(client, collection, "dense", embed_dense(query), qfilter, k)
    sparse_hits = _search_one(
        client, collection, "sparse", models.SparseVector(**embed_sparse(query)), qfilter, k
    )
    fused = rrf_fuse([dense_hits, sparse_hits], RETRIEVAL.rrf_k)

    if mode == "hybrid":
        return fused[:top_k]

    if mode == "hybrid_rerank":
        from .rerank import rerank   # imported here so dense/hybrid never load the model
        return rerank(query, fused[:k], top_k)

    raise ValueError(f"Unknown retrieval mode: {mode!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--mode", default=None, help="dense | hybrid | hybrid_rerank (default: .env)")
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--collection", default=None, help="default: QDRANT_COLLECTION_NAME; use barq_manual for Track B")
    args = ap.parse_args()

    results = search(args.query, mode=args.mode, top_k=args.top_k, collection=args.collection)
    print(f"mode={args.mode or RETRIEVAL.mode}  query={args.query!r}\n")
    for i, c in enumerate(results, 1):
        print(f"{i}. {c.payload.get('section_label', c.number)} [{c.section}] score={c.score:.4f}  {c.payload.get('title', '')}")

"""
Cross-encoder reranking (S2.4 to be used only in RETRIEVAL_MODE=hybrid_rerank).
"""

from fastembed.rerank.cross_encoder import TextCrossEncoder

from ..config import RETRIEVAL
from .hybrid_search import RetrievedChunk

_model: TextCrossEncoder | None = None   # loaded once, on first use


def _get_model() -> TextCrossEncoder:
    global _model
    if _model is None:
        _model = TextCrossEncoder(model_name=RETRIEVAL.rerank_model)
    return _model


def rerank(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Re-score `chunks` against `query` with the cross-encoder to return the best top_k"""
    if not chunks:
        return []

    scores = list(_get_model().rerank(query, [c.text for c in chunks]))

    rescored = [
        RetrievedChunk(**{**c.__dict__, "score": float(s)})
        for c, s in zip(chunks, scores)
    ]
    # Best first, on a tie, smaller point_id first (deterministic).
    rescored.sort(key=lambda c: (-c.score, c.point_id))
    return rescored[:top_k]

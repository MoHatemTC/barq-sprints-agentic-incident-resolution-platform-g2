"""
Cross-encoder reranking and diversification (S2.4 / S4.3).
Applied in RETRIEVAL_MODE=hybrid_rerank.

Enhancements:
1. Score Normalization: Transforms raw cross-encoder logits via sigmoid into [0.0, 1.0]
   probabilities so scores make intuitive sense and reflect genuine match confidence.
2. Diversity-Aware Reranking (pyversity): Balances relevance and diversity using DPP / MMR
   to prevent near-duplicate chunks from dominating the top-k results.
"""

import logging
import math
from typing import List

import numpy as np
from fastembed.rerank.cross_encoder import TextCrossEncoder

from ..config import RETRIEVAL
from .hybrid_search import RetrievedChunk

logger = logging.getLogger(__name__)

_model: TextCrossEncoder | None = None   # loaded once, on first use

try:
    from pyversity import diversify, Strategy
    PYVERSITY_AVAILABLE = True
except ImportError:
    PYVERSITY_AVAILABLE = False
    diversify = None
    Strategy = None


def _sigmoid(logit: float) -> float:
    """Map unbounded cross-encoder logit to [0.0, 1.0] probability."""
    # Prevent overflow in math.exp
    if logit >= 20.0:
        return 1.0
    if logit <= -20.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-logit))


def _get_model() -> TextCrossEncoder:
    global _model
    if _model is None:
        _model = TextCrossEncoder(model_name=RETRIEVAL.rerank_model)
    return _model


def _compute_chunk_features(texts: List[str]) -> np.ndarray:
    """
    Compute fast, lightweight TF-IDF document features for diversity calculation.
    Purely local with sub-millisecond execution.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=128, stop_words="english")
        matrix = vec.fit_transform(texts).toarray()
        if matrix.shape[1] == 0:
            return np.eye(len(texts))
        return matrix
    except Exception:
        # Fallback to simple identity representation if sklearn fails
        return np.eye(len(texts))


def rerank(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """
    Re-score `chunks` against `query` with the cross-encoder, normalize logits
    into [0.0, 1.0] range, and apply diversification (pyversity) to return the best top_k.
    """
    if not chunks:
        return []

    # The title tells the cross-encoder what a "Resolution." chunk is the resolution of.
    texts = [f"{c.payload.get('title', '')}\n{c.text}".strip() for c in chunks]
    raw_scores = list(_get_model().rerank(query, texts))

    # 1. Normalize logits via sigmoid into [0.0, 1.0] probabilities
    normalized_scores = [_sigmoid(float(s)) for s in raw_scores]

    rescored = [
        RetrievedChunk(**{**c.__dict__, "score": float(score)})
        for c, score in zip(chunks, normalized_scores)
    ]

    # If top_k >= len(chunks), no selection needed; just sort by normalized score
    if len(rescored) <= top_k:
        rescored.sort(key=lambda c: (-c.score, c.point_id))
        return rescored

    # 2. Apply pyversity diversification if available
    if PYVERSITY_AVAILABLE and len(rescored) > 1:
        try:
            embeddings = _compute_chunk_features(texts)
            scores_arr = np.array(normalized_scores, dtype=np.float64)

            # Strategy.DPP provides optimal balance between high relevance and structural variety
            result = diversify(
                embeddings=embeddings,
                scores=scores_arr,
                k=top_k,
                strategy=Strategy.DPP,
                diversity=0.35,  # 0.35 preserves strong relevance while penalizing duplicates
            )
            selected_indices = list(result.indices)
            return [rescored[i] for i in selected_indices]
        except Exception as exc:
            logger.warning("pyversity diversification fallback: %s", exc)

    # 3. Fallback: Pure relevance ranking by normalized score
    rescored.sort(key=lambda c: (-c.score, c.point_id))
    return rescored[:top_k]


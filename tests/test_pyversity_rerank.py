"""
Tests for enhanced cross-encoder reranker with sigmoid normalization
and pyversity diversification (S4.3).
"""

from unittest.mock import MagicMock, patch
import pytest

from src.retrieval.hybrid_search import RetrievedChunk
from src.retrieval.rerank import rerank, _sigmoid, _compute_chunk_features


def test_sigmoid_normalization():
    """Verify that logits map strictly to [0.0, 1.0]."""
    assert _sigmoid(0.0) == 0.5
    assert _sigmoid(5.0) > 0.99
    assert _sigmoid(-5.0) < 0.01
    assert _sigmoid(25.0) == 1.0
    assert _sigmoid(-25.0) == 0.0


def test_rerank_empty_chunks():
    """Verify rerank handles empty inputs safely."""
    assert rerank("query", [], top_k=5) == []


def test_rerank_normalizes_and_ranks_correctly():
    """Rerank should return normalized scores with relevant chunk first."""
    c1 = RetrievedChunk(
        point_id="p1",
        number="KB0001",
        section="Resolution",
        text="Restart the VPN gateway service to fix credential cache.",
        score=0.1,
        payload={"title": "VPN Failure"},
    )
    c2 = RetrievedChunk(
        point_id="p2",
        number="KB0002",
        section="Resolution",
        text="Clean printer spooler and check print paper tray.",
        score=0.2,
        payload={"title": "Printer Jam"},
    )

    results = rerank("VPN gateway authentication error", [c1, c2], top_k=2)

    assert len(results) == 2
    # VPN chunk must rank first
    assert results[0].number == "KB0001"
    # Scores must be within valid probability range
    assert 0.0 <= results[0].score <= 1.0
    assert 0.0 <= results[1].score <= 1.0
    # Relevant chunk should have a meaningfully higher score than irrelevant chunk
    assert results[0].score > results[1].score


def test_compute_chunk_features():
    """Verify fast feature matrix generation for diversity."""
    texts = [
        "Network connection timed out on port 443",
        "Printer toner low on floor 3",
        "VPN login failed after password change",
    ]
    matrix = _compute_chunk_features(texts)
    assert matrix.shape[0] == 3
    assert matrix.shape[1] > 0


def test_rerank_pyversity_fallback_when_unavailable():
    """Verify fallback to pure relevance sorting when pyversity raises an error."""
    c1 = RetrievedChunk(
        point_id="p1",
        number="KB0001",
        section="body",
        text="VPN gateway setup",
        score=0.0,
        payload={"title": "VPN"},
    )
    c2 = RetrievedChunk(
        point_id="p2",
        number="KB0002",
        section="body",
        text="VPN client cache",
        score=0.0,
        payload={"title": "VPN"},
    )
    c3 = RetrievedChunk(
        point_id="p3",
        number="KB0003",
        section="body",
        text="Printer offline",
        score=0.0,
        payload={"title": "Printer"},
    )

    with patch("src.retrieval.rerank.PYVERSITY_AVAILABLE", False):
        results = rerank("VPN setup", [c1, c2, c3], top_k=2)
        assert len(results) == 2
        assert all(0.0 <= r.score <= 1.0 for r in results)

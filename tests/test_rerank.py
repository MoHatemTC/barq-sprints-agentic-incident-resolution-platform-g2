# Integration tests for the cross-encoder reranker (real model, no Qdrant) to Run it "pytest tests/test_rerank.py -v"
from src.retrieval.hybrid_search import RetrievedChunk
from src.retrieval.rerank import rerank


def chunk(pid, text):
    return RetrievedChunk(point_id=pid, number=pid, section="", text=text, score=0.0, payload={})


PASSAGES = [
    chunk("printer", "Print jobs queue but nothing prints. Restart the print spooler service."),
    chunk("vpn", "VPN authentication fails after a password change. Clear cached credentials."),
    chunk("battery", "Laptop battery drains rapidly. Check for background apps and power plan."),
]


def test_relevant_passage_is_ranked_first():
    best = rerank("vpn fails after I changed my password", PASSAGES, top_k=1)
    assert best[0].point_id == "vpn"


def test_returns_top_k_sorted_best_first():
    out = rerank("vpn password", PASSAGES, top_k=2)
    assert len(out) == 2
    assert out[0].score >= out[1].score


def test_scores_are_replaced_not_kept():
    # input scores are all 0.0 , output scores must be the cross-encoder's numbers
    out = rerank("vpn password", PASSAGES, top_k=3)
    assert any(c.score != 0.0 for c in out)


def test_same_input_twice_gives_same_order():
    a = [c.point_id for c in rerank("printer not printing", PASSAGES, top_k=3)]
    b = [c.point_id for c in rerank("printer not printing", PASSAGES, top_k=3)]
    assert a == b


def test_empty_input_returns_empty():
    assert rerank("anything", [], top_k=5) == []

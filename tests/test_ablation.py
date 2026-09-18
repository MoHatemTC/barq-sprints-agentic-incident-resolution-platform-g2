# Pure unit tests for the ablation grading logic. No models, no Qdrant.
# Run: pytest tests/test_ablation.py -v
import pytest

from eval.ablation import grade, sparse_wins


def test_grade_worked_example():
    # expected {KB0013, KB0021}; 5 chunks returned, two of them from KB0013
    g = grade(["KB0013", "KB0013", "KB0008", "KB0021", "KB0009"], ["KB0013", "KB0021"])
    assert g["returned"] == ["KB0013", "KB0008", "KB0021", "KB0009"]   # de-duplicated, order kept
    assert g["precision"] == pytest.approx(2 / 4)
    assert g["recall"] == pytest.approx(2 / 2)
    assert g["hit"] is True
    assert g["first_rank"] == 1 and g["rr"] == 1.0


def test_grade_total_miss():
    g = grade(["KB0002", "KB0003"], ["KB0001"])
    assert g["precision"] == 0.0 and g["recall"] == 0.0
    assert g["hit"] is False and g["first_rank"] is None and g["rr"] == 0.0


def test_grade_first_hit_at_rank_3_gives_rr_one_third():
    g = grade(["KB0002", "KB0003", "KB0001"], ["KB0001"])
    assert g["first_rank"] == 3
    assert g["rr"] == pytest.approx(1 / 3)


def test_sparse_wins_counts_only_rank_improvements():
    items = {"Q1": {"query": "q1", "expected_articles": ["KB0001"]},
             "Q2": {"query": "q2", "expected_articles": ["KB0002"]},
             "Q3": {"query": "q3", "expected_articles": ["KB0003"]},
             "Q4": {"query": "q4", "expected_articles": []}}
    row = lambda qid, rank, ans=True: {"query_id": qid, "answerable": ans, "first_rank": rank, "returned": []}
    dense = [row("Q1", None), row("Q2", 3), row("Q3", 1), row("Q4", None, ans=False)]
    hybrid = [row("Q1", 1), row("Q2", 1), row("Q3", 1), row("Q4", 1, ans=False)]

    wins = sparse_wins(dense, hybrid, items)

    assert [w["query_id"] for w in wins] == ["Q1", "Q2"]   # miss->1 and 3->1 count; 1->1 and unanswerable don't
    assert wins[0]["dense_rank"] is None and wins[0]["hybrid_rank"] == 1

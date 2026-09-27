# Pure unit tests for the ablation grading logic. No models, no Qdrant.
# Run: pytest tests/test_ablation.py -v
import pytest

from eval.ablation import (
    MANUAL_ARMS,
    arm_regressions,
    grade,
    quality_fingerprint,
    sparse_wins,
    summarise_arm,
)


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


def test_sparse_wins_reports_recovered_and_rank_kinds():
    items = {"Q1": {"query": "q1", "expected_articles": ["KB0001"]},
             "Q2": {"query": "q2", "expected_articles": ["KB0002"]},
             "Q3": {"query": "q3", "expected_articles": ["KB0003"]},
             "Q4": {"query": "q4", "expected_articles": []}}
    row = lambda qid, rank, returned, ans=True: {"query_id": qid, "answerable": ans, "first_rank": rank, "returned": returned}
    dense = [row("Q1", None, []), row("Q2", 3, ["x", "y", "KB0002"]), row("Q3", 1, ["KB0003"]), row("Q4", None, [], ans=False)]
    hybrid = [row("Q1", 1, ["KB0001"]), row("Q2", 1, ["KB0002"]), row("Q3", 1, ["KB0003"]), row("Q4", 1, ["z"], ans=False)]

    wins = sparse_wins(dense, hybrid, items)

    assert [(w["query_id"], w["kind"]) for w in wins] == [("Q1", "recovered"), ("Q2", "rank")]
    assert wins[0]["recovered"] == ["KB0001"]            # missing from dense, present in hybrid
    assert wins[1]["dense_rank"] == 3 and wins[1]["hybrid_rank"] == 1


def test_sparse_wins_recovered_second_article_of_multi_doc():
    items = {"Q": {"query": "0x8004010F", "expected_articles": ["KB0002", "KB0025"]}}
    dense = [{"query_id": "Q", "answerable": True, "first_rank": 1, "returned": ["KB0002", "KB0013"]}]
    hybrid = [{"query_id": "Q", "answerable": True, "first_rank": 1, "returned": ["KB0002", "KB0025"]}]
    wins = sparse_wins(dense, hybrid, items)
    assert len(wins) == 1 and wins[0]["kind"] == "recovered" and wins[0]["recovered"] == ["KB0025"]


# --------------------------------------------------------------------------- #
# S2.6: the manual stressor arms
# --------------------------------------------------------------------------- #


def row(qid, returned, expected=("7.1",), *, answerable=True, requires_extractor=True,
        capability="table", hit_from=()):
    """One stressor-arm row in the shape run_stressor_arm produces.

    ``hit`` is worked out the way grade() works it out -- an expected section is
    among the returned ones -- so a test cannot assert a hit by accident.  An
    unanswerable row carries no ``hit`` at all, which is what the harness
    produces: grade() is only applied to answerable rows.
    """
    record = {"query_id": qid, "returned": returned, "answerable": answerable,
              "requires_extractor": requires_extractor, "capability_class": capability,
              "hit_from": list(hit_from), "ms": 900.0}
    if answerable:
        hit = bool(set(expected) & set(returned))
        record.update({"hit": hit, "precision": 1.0 if hit else 0.0,
                       "recall": 1.0 if hit else 0.0, "rr": 1.0 if hit else 0.0})
    return record


def test_summarise_arm_scores_only_answerable_rows():
    rows = [row("Q1", ["7.1"], ("7.1",)), row("Q2", ["7.2"], ("7.1",)),
            row("Q3", ["7.4"], answerable=False)]
    s = summarise_arm(rows)
    assert s["hit_rate"] == pytest.approx(0.5)
    assert s["precision"] == pytest.approx(0.5)
    assert s["mrr"] == pytest.approx(0.5)
    assert s["answered_unanswerable"] == 1 and s["unanswerable"] == 1


def test_summarise_arm_reports_extractor_rows_separately():
    """A row answerable from plain prose must not flatter the extractor arm."""
    rows = [row("Q1", ["7.1"], ("7.1",), requires_extractor=True),
            row("Q2", ["2.1"], ("2.1",), requires_extractor=False)]
    s = summarise_arm(rows)
    assert s["hit_rate"] == pytest.approx(1.0)
    assert s["extractor_dependent_hit_rate"] == pytest.approx(1.0)


def test_summarise_arm_omits_the_extractor_column_when_nothing_depends_on_one():
    s = summarise_arm([row("Q1", ["2.1"], ("2.1",), requires_extractor=False)])
    assert "extractor_dependent_hit_rate" not in s


def test_summarise_arm_handles_no_answerable_rows_at_all():
    s = summarise_arm([row("Q1", ["7.1"], answerable=False)])
    assert s["hit_rate"] == 0.0 and s["mrr"] == 0.0 and s["answered_unanswerable"] == 1


def test_arm_regressions_reports_gains_and_losses():
    base = {"Q1": row("Q1", ["7.2"]), "Q2": row("Q2", ["11.7"], ("11.7",)), "Q3": row("Q3", ["2.1"], ("2.1",))}
    ext = {"Q1": row("Q1", ["7.1"], ("7.1",), hit_from=["tables"]),
           "Q2": row("Q2", ["11.2"], ("11.7",)),
           "Q3": row("Q3", ["2.1"], ("2.1",))}
    changes = arm_regressions(base, ext)
    by_id = {c["query_id"]: c for c in changes}
    assert set(by_id) == {"Q1", "Q2"}
    assert by_id["Q1"]["hit_from"] == ["tables"]
    assert by_id["Q2"]["capability_class"] == "table"


def test_arm_regressions_skips_unanswerable_rows():
    """An unanswerable row has no hit to diff; it is scored on returning anything."""
    base = {"Q1": row("Q1", [], answerable=False)}
    ext = {"Q1": row("Q1", ["7.1"], answerable=False)}
    assert arm_regressions(base, ext) == []


def test_arm_regressions_is_empty_when_nothing_changes_hands():
    base = {f"Q{i}": row(f"Q{i}", ["7.1"], ("7.1",)) for i in range(3)}
    assert arm_regressions(base, dict(base)) == []


def test_quality_fingerprint_drops_timing():
    summaries = {"baseline": {"precision": 0.2, "p50_ms": 900.0, "p95_ms": 1100.0, "mrr": 0.6}}
    fp = quality_fingerprint(summaries)
    assert fp["baseline"] == {"precision": 0.2, "mrr": 0.6}
    assert "p50_ms" not in fp["baseline"] and "p95_ms" not in fp["baseline"]


def test_quality_fingerprint_ignores_a_latency_only_change():
    """Two runs of the same corpus differ in timing and must compare equal."""
    a = quality_fingerprint({"stressor": {"hit_rate": 1.0, "p95_ms": 6043.0}})
    b = quality_fingerprint({"stressor": {"hit_rate": 1.0, "p95_ms": 1318.3}})
    assert a == b


def test_manual_arms_are_three_filters_over_one_collection():
    """Not three collections. Every arm searches QDRANT.collection_name and differs
    only in which points are eligible, so the arms actually compete for the same
    top-k. Separate collections would make a no-regression result unfalsifiable."""
    assert set(MANUAL_ARMS) == {"baseline", "stressor", "combined"}
    assert MANUAL_ARMS["baseline"].is_stressor is False   # KB articles only
    assert MANUAL_ARMS["stressor"].is_stressor is True    # extracted pages only
    assert MANUAL_ARMS["combined"].is_stressor is None    # everything


def test_arm_filters_are_mutually_exclusive_but_both_live_in_combined():
    from src.retrieval.filters import build_qdrant_filter

    base = build_qdrant_filter(MANUAL_ARMS["baseline"])
    stress = build_qdrant_filter(MANUAL_ARMS["stressor"])
    comb = build_qdrant_filter(MANUAL_ARMS["combined"])

    # baseline excludes stressor points; stressor requires them; neither can return
    # the other's points.
    assert any(c.key == "is_stressor" for c in base.must_not)
    assert any(c.key == "is_stressor" and c.match.value is True for c in stress.must)
    # combined is unconstrained on is_stressor: a live query sees both halves.
    assert not any(c.key == "is_stressor" for c in comb.must)
    assert not any(c.key == "is_stressor" for c in comb.must_not)


def test_grade_gives_a_stressor_row_nothing_to_match_against():
    """The reason stressor rows carry expected_articles: [].

    grade() divides by the size of the expected set, so an empty one scores 0.0
    recall and a first_rank of 1 for whatever came back. A section-level hit has
    to be graded against expected_sections, and there is no KB number that
    resolves to a manual section.
    """
    g = grade(["7.1", "7.2"], [])
    assert g["recall"] == 0.0
    assert g["hit"] is False

"""``geometry.containment`` -- the bbox primitive both extractors now share.

The two callers want different things from it (is a table inside another table,
is a checkbox clear of a form value) but they were asking it the same way, and
two copies of one bit of arithmetic is how the two start disagreeing. So the
contract is pinned here directly, rather than only through the nested-table and
checkbox tests that happen to exercise it.

Rects are ``(x0, y0, x1, y1)`` in PDF points.

Run: pytest tests/test_extractors_geometry.py -v
"""

from __future__ import annotations

import pytest

from src.retrieval.extractors.geometry import containment


def test_identical_rects_are_fully_contained():
    assert containment((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)


def test_a_rect_inside_a_larger_one_scores_one_regardless_of_fill():
    """Containment, not coverage: a small box in a big one is still inside it."""
    assert containment((2, 2, 4, 4), (0, 0, 100, 100)) == pytest.approx(1.0)


def test_disjoint_rects_score_zero():
    assert containment((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0


def test_touching_edges_score_zero():
    """Shared edge, zero overlap area -- not a containment."""
    assert containment((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0


def test_half_overlap_is_a_half():
    # 10x10 box against a neighbour sharing the left 5pt strip: 50/100 of the area.
    assert containment((0, 0, 10, 10), (-5, 0, 5, 10)) == pytest.approx(0.5)


def test_quarter_overlap_scores_by_area_not_by_side():
    """Overlap is measured as a fraction of area, so a 50% width overlap on half
    the height is a quarter -- the reason callers can threshold at 0.8 and mean
    'almost entirely inside'."""
    assert containment((0, 0, 10, 10), (5, 0, 15, 5)) == pytest.approx(0.25)


def test_a_degenerate_rect_has_no_area_to_be_contained():
    for degenerate in [(5, 5, 5, 10), (5, 5, 10, 5), (5, 5, 5, 5), (5, 5, 4, 4)]:
        assert containment(degenerate, (0, 0, 100, 100)) == 0.0


def test_result_is_bounded_to_zero_one():
    """Even when the outer rect is smaller than the inner one, the answer is a
    fraction of the inner, so it can never exceed 1.0."""
    small = (0, 0, 10, 10)
    big = (-100, -100, 200, 200)
    assert 0.0 <= containment(small, big) <= 1.0
    assert 0.0 <= containment(big, small) <= 1.0
    assert containment(big, small) < 1.0, "a box larger than its container is not contained"


def test_translation_does_not_change_the_score():
    """Both call sites pass rects from different spaces, so an offset must not
    change the answer."""
    assert containment((100, 100, 110, 110), (100, 100, 200, 200)) == pytest.approx(
        containment((0, 0, 10, 10), (0, 0, 100, 100))
    )


def test_both_extractors_use_the_one_implementation():
    """If this fails, someone re-copied the arithmetic instead of importing it."""
    import src.retrieval.extractors.layout as layout
    import src.retrieval.extractors.tables as tables

    assert layout.containment is tables.containment
    for module in (layout, tables):
        assert "_containment" not in vars(module), f"{module.__name__} still defines its own copy"

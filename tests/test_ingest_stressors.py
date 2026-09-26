"""Stressor routing: which extractor each page gets, and what gets indexed.

Routing is the seam between the parser and the extractors, and it is the part
with no ground truth of its own -- so the manual fixes which pages are claimed
and the synthetic pages fix the two judgements the manual cannot make in
isolation: whether a table is really split across a page break, and what a page
with nothing but a screenshot is routed to.

Nothing here embeds or touches Qdrant; ``build_stressor_points`` is exercised
with the embedding calls stubbed, because the test is about which points and
which payload, and the embedding model is not installed in CI.

Run: pytest tests/test_ingest_stressors.py -v
"""
from __future__ import annotations

import io

import pymupdf
import pytest

from src.config import QDRANT
from src.retrieval import ingest_stressors as mod
from src.retrieval.ingest import (
    _delete_article,
    _stored_hashes,
    drop_stressors,
    ingest_stressors,
)
from src.retrieval.ingest_stressors import (
    LAYOUT,
    OCR,
    STRESSOR_COLLECTION,
    TABLES,
    TEXT,
    PageRoute,
    build_stressor_points,
    route_page,
    route_stressor_pages,
)
from src.retrieval.manual_parser import parse_manual

MANUAL = "data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf"

# The manual pages the routing must claim, and the section that owns each.  p26
# is in the list on purpose: it is the second half of 7.1's journal and belongs
# to no section's page_start, so it is claimed by the table on p25.
EXPECTED_PAGES = {
    8: "2.1", 10: "3.3", 15: "5.2", 25: "7.1", 26: "7.1", 29: "8.3",
    35: "10.4", 41: "11.7", 42: "11.8", 44: "12.2", 49: "Appendix C",
}

# The four page breaks the manual actually splits a table across.
REAL_SPLITS = [(25, 26), (31, 32), (47, 48), (51, 52)]


@pytest.fixture(scope="module")
def routes() -> list[PageRoute]:
    return route_stressor_pages()


@pytest.fixture(scope="module")
def manual() -> pymupdf.Document:
    return pymupdf.open(MANUAL)


@pytest.fixture(scope="module")
def monkeypatch_module():
    """Module-scoped monkeypatch, so the embedding stub can outlive one test."""
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    yield patcher
    patcher.undo()


# --------------------------------------------------------------------------- #
# which pages get claimed
# --------------------------------------------------------------------------- #


def test_routing_claims_every_declared_stressor_section(routes):
    assert {r.page_number: r.section_id for r in routes} == EXPECTED_PAGES


def test_every_routed_section_is_one_the_tables_module_declares_a_stressor(routes):
    assert {r.section_id for r in routes} <= set(mod.STRESSOR_SECTIONS)


def test_routing_is_page_order_and_has_no_duplicates(routes):
    pages = [r.page_number for r in routes]
    assert pages == sorted(pages)
    assert len(pages) == len(set(pages))


def test_prose_page_never_claims_a_table_extractor(manual):
    """A page of running text is the case where find_tables wastes real time."""
    route = route_page(manual[0], 1, "front-matter")
    assert TEXT in route.extractors
    assert TABLES not in route.extractors
    assert "ruled grid" not in route.reasons.get(TABLES, "")


# --------------------------------------------------------------------------- #
# which extractors, and why recorded
# --------------------------------------------------------------------------- #


def test_routed_reasons_explain_every_non_text_extractor(routes):
    for route in routes:
        for name in route.extractors:
            if name != TEXT:
                assert route.reasons.get(name), f"page {route.page_number}: {name} has no reason"


def test_table_pages_get_the_table_extractor_and_a_reason(routes):
    with_tables = [r for r in routes if TABLES in r.extractors]
    assert {r.page_number for r in with_tables} == {8, 10, 15, 25, 26, 29, 41, 44, 49}
    assert all("grid" in r.reasons[TABLES] for r in with_tables)


def test_screenshot_page_is_routed_to_ocr_not_ocrred(manual):
    """p35's approval form is a picture; the route must say so, not invent text."""
    route = route_page(manual[34], 35, "10.4")
    assert OCR in route.extractors
    assert "no text" in route.reasons[OCR]
    assert TABLES not in route.extractors          # the form is not a ruled grid


def test_routes_page_35_layout_point_carries_no_form_fields_from_the_picture(manual):
    from src.retrieval.extractors.layout import extract_layout

    result = extract_layout(manual[34], 35)
    assert result.has_text_layer
    assert result.needs_ocr is False
    assert result.form_fields == []                 # the fields are inside the raster
    assert result.ocr_regions                       # ... and the region is reported


def test_layout_is_routed_only_where_positioned_text_exists(manual):
    route = route_page(manual[0], 1, "front-matter")
    assert LAYOUT in route.extractors
    assert "positioned" in route.reasons[LAYOUT]


# --------------------------------------------------------------------------- #
# split detection: the judgement the routing turns on
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("first,second", REAL_SPLITS)
def test_real_splits_are_detected(manual, first, second):
    assert mod._continues_onto(manual[first - 1 + 1], manual[first - 1]) is True, \
        f"p{first} should be recognised as continuing onto p{second}"


def test_real_splits_detected_are_exactly_the_four(manual):
    found = [(p, p + 1) for p in range(1, manual.page_count)
             if mod._continues_onto(manual[p], manual[p - 1])]
    assert found == REAL_SPLITS


def _ruled_table_page(doc, top: float, bottom: float, cols: int = 3, rows: int = 3,
                      label: str = "cell", page_index: int = 0) -> None:
    """A grid ``find_tables`` will actually see: every cell edge ruled, and text
    in every cell.  Ruling only the outer box is invisible to it, which would
    make every assertion below pass for the wrong reason."""
    while doc.page_count <= page_index:
        doc.new_page(width=595, height=842)
    page = doc[page_index]
    left, right = 40.0, page.rect.width - 40.0
    step_x = (right - left) / cols
    step_y = (bottom - top) / rows
    for c in range(cols):
        for r in range(rows):
            x0, y0 = left + c * step_x, top + r * step_y
            x1, y1 = x0 + step_x, y0 + step_y
            for a, b in ((pymupdf.Point(x0, y0), pymupdf.Point(x1, y0)),
                         (pymupdf.Point(x0, y1), pymupdf.Point(x1, y1)),
                         (pymupdf.Point(x0, y0), pymupdf.Point(x0, y1)),
                         (pymupdf.Point(x1, y0), pymupdf.Point(x1, y1))):
                page.draw_line(a, b, width=0.6)
            page.insert_text((x0 + 2, y0 + step_y / 2), f"{label} {r}{c}", fontsize=6)


def _committed(doc: pymupdf.Document) -> pymupdf.Document:
    """Round-trip so drawn lines and inserted text reach the content stream."""
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return pymupdf.open(stream=buffer.getvalue(), filetype="pdf")


def _ruled_page(top: float, bottom: float, cols: int, rows: int = 3) -> pymupdf.Document:
    doc = pymupdf.open()
    _ruled_table_page(doc, top, bottom, cols, rows)
    return _committed(doc)


def test_synthetic_grids_are_detected_by_find_tables():
    """Guard for the helper: a grid it cannot see would make every split test
    below pass without testing anything."""
    page = _ruled_page(top=700, bottom=830, cols=4)[0]
    tables = page.find_tables(strategy="lines").tables
    assert tables, "helper draws a grid find_tables cannot see"
    assert tables[-1].col_count == 4


def test_a_table_split_across_two_pages_is_a_split():
    """The positive case: a grid running off the foot of one page, and the same
    grid one column narrower at the top of the next -- the tolerance the manual's
    own 7.1 journal needs."""
    doc = pymupdf.open()
    _ruled_table_page(doc, top=700, bottom=830, cols=4, page_index=0)
    _ruled_table_page(doc, top=60, bottom=300, cols=3, page_index=1)
    doc = _committed(doc)
    assert mod._continues_onto(doc[1], doc[0]) is True


def test_two_complete_tables_foot_and_head_are_not_a_split():
    """p27->p28: a 3-column table at the foot of one page, an unrelated
    8-column one at the top of the next.  Position alone would call that a
    split; the column count is what tells them apart."""
    doc = pymupdf.open()
    _ruled_table_page(doc, top=700, bottom=830, cols=3, page_index=0)
    _ruled_table_page(doc, top=60, bottom=300, cols=8, page_index=1)
    doc = _committed(doc)
    assert doc[0].find_tables(strategy="lines").tables, "helper must draw a real grid"
    assert mod._continues_onto(doc[1], doc[0]) is False


def test_table_ending_mid_page_is_not_a_split():
    page = _ruled_page(top=200, bottom=500, cols=4)[0]
    assert mod._continues_onto(page, page) is False


def test_a_narrower_table_at_the_top_of_the_next_page_is_not_a_split():
    """Three columns against eight is a different table, not a re-detected
    split; this is the case a zero tolerance would have let through."""
    doc = pymupdf.open()
    _ruled_table_page(doc, top=700, bottom=830, cols=3, page_index=0)
    _ruled_table_page(doc, top=60, bottom=300, cols=8, page_index=1)
    doc = _committed(doc)
    assert mod._continues_onto(doc[1], doc[0]) is False


def test_a_table_ending_a_third_of_the_way_up_the_page_is_not_a_split():
    page = _ruled_page(top=200, bottom=500, cols=4)[0]
    assert page.find_tables(strategy="lines").tables, "helper must draw a real grid"
    assert mod._continues_onto(page, page) is False


def test_prose_page_after_a_split_table_is_not_a_split():
    doc = pymupdf.open()
    _ruled_table_page(doc, top=700, bottom=830, cols=4, page_index=0)
    doc.new_page(width=595, height=842)
    doc[1].insert_text((40, 60), "A new section starts here.", fontsize=9)
    doc = _committed(doc)
    assert mod._continues_onto(doc[1], doc[0]) is False


def test_empty_page_after_a_split_table_is_not_a_split():
    doc = pymupdf.open()
    _ruled_table_page(doc, top=700, bottom=830, cols=4)
    doc.new_page(width=595, height=842)
    doc = _committed(doc)
    assert mod._continues_onto(doc[1], doc[0]) is False


# --------------------------------------------------------------------------- #
# the points that get indexed
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def stubbed_embeddings(monkeypatch_module):
    """Replace the two embedding calls so the test needs no model."""
    calls: list[str] = []
    monkeypatch_module.setattr(mod, "embed_dense", lambda text: calls.append("dense") or [0.0] * 8)
    monkeypatch_module.setattr(mod, "embed_sparse",
                               lambda text, *a, **k: calls.append("sparse") or {"indices": [1], "values": [1.0]})
    # no dimension stub: the shared collection already has the right vectors, so a
    # stressor run never has to know or declare their size.
    return calls


@pytest.fixture(scope="module")
def stubbed_model(monkeypatch_module):
    """_ensure_collection still checks the embedding model before letting a
    stressor run write into the collection. These are the stubs that let it pass."""
    import src.retrieval.ingest as ingest_mod
    monkeypatch_module.setattr(ingest_mod, "get_dense_dimension", lambda: 8)
    monkeypatch_module.setattr(ingest_mod, "get_model_fingerprint", lambda: "stub")


@pytest.fixture(scope="module")
def points_and_stats(stubbed_embeddings):
    return build_stressor_points()


def test_every_stressor_page_yields_at_least_one_point(points_and_stats):
    points, stats = points_and_stats
    assert stats["pages"] == len(EXPECTED_PAGES)
    pages = {p for point in points for p in point.payload["extractor_pages"]}
    assert pages == set(EXPECTED_PAGES)


def test_point_kinds_are_table_layout_key_value_and_ocr(points_and_stats):
    points, stats = points_and_stats
    assert set(stats["by_kind"]) == {"table", "layout", "form", "ocr_region"}
    assert all(stats["by_kind"][k] > 0 for k in stats["by_kind"])


def test_stressor_point_ids_are_deterministic(points_and_stats):
    first, _ = points_and_stats
    second, _ = build_stressor_points()
    assert [p.id for p in first] == [p.id for p in second]


def test_stressor_payload_keeps_every_manual_payload_key(points_and_stats):
    """A stressor point has to be filterable by the same fields as a manual one."""
    from src.retrieval.ingest_manual import build_points

    manual_keys = set(build_points(parse_manual()[:1])[0].payload)
    points, _ = points_and_stats
    for point in points:
        assert manual_keys <= set(point.payload), f"missing {manual_keys - set(point.payload)}"


def test_stressor_points_are_marked_and_carry_provenance(points_and_stats):
    points, _ = points_and_stats
    for point in points:
        payload = point.payload
        assert payload["is_stressor"] is True
        assert payload["source"] == "stressor"
        assert payload["capability_class"]
        assert payload["extractor"] in {TABLES, LAYOUT, OCR}
        assert payload["provenance"]
        assert payload["extractor_pages"]


def test_ocr_points_record_the_region_without_claiming_it_was_read(points_and_stats):
    points, _ = points_and_stats
    ocr_points = [p for p in points if p.payload["capability_class"] == "ocr"]
    assert ocr_points
    for point in ocr_points:
        assert point.payload["provenance"]["ocr_applied"] is False
        assert point.payload["provenance"]["region_bbox"]
        assert "not indexed as text" in point.payload["text"]


def test_table_points_carry_the_flags_the_measurement_needs(points_and_stats):
    points, _ = points_and_stats
    tables = [p for p in points if p.payload["capability_class"] == "table"]
    assert any(p.payload["provenance"]["has_merged_header"] for p in tables)
    assert any(p.payload["provenance"]["has_nested"] for p in tables)
    assert any(p.payload["provenance"]["spans_page_boundary"] for p in tables)
    assert any(p.payload["provenance"]["issues"] for p in tables)
    for point in tables:
        assert point.payload["provenance"]["columns"]


def test_the_split_journal_is_indexed_once_as_one_table(points_and_stats):
    """7.1's journal is one table over two pages; indexing the halves separately
    would make a question about a p26 row match a fragment."""
    points, stats = points_and_stats
    assert stats["page_crossing"] == [[25, 26]]
    journal = [p for p in points
               if p.payload["section_id"] == "7.1"
               and p.payload["provenance"].get("spans_page_boundary")]
    assert len(journal) == 1
    assert journal[0].payload["extractor_pages"] == [25, 26]


def test_nested_tables_are_attached_to_their_parent_not_indexed_separately(points_and_stats):
    points, stats = points_and_stats
    assert stats["nested_tables"] > 0
    assert all(p.payload["extractor"] == TABLES for p in points
               if p.payload["capability_class"] == "table")


# --------------------------------------------------------------------------- #
# upsert
# --------------------------------------------------------------------------- #


class FakeClient:
    """Records what was asked of it. The shared collection already exists."""

    def __init__(self):
        self.upserted: list = []
        self.deleted: list = []

    def collection_exists(self, name):
        return True   # the KB collection is already up, created by ingest_articles

    def retrieve(self, name, ids=None):
        return [type("M", (), {"payload": {"fingerprint": "stub", "_is_marker": True}})()]

    def upsert(self, name, points):
        self.upserted_name = name
        self.upserted.extend(points)

    def count(self, name, exact=True, count_filter=None):
        return type("C", (), {"count": len(self.upserted)})()

    def delete(self, name, points_selector):
        self.deleted.append((name, points_selector))

    def get_collection(self, name):
        return type("I", (), {"points_count": len(self.upserted) + 166})()


def test_ingest_targets_the_shared_kb_collection(points_and_stats, stubbed_embeddings, stubbed_model):
    """The mentor correction: one search space. A stressor point that cannot be
    returned by a KB query proves nothing about the extractors."""
    points, _ = points_and_stats
    client = FakeClient()
    stats = ingest_stressors(client=client)
    assert client.upserted_name == QDRANT.collection_name
    assert stats["collection"] == QDRANT.collection_name
    assert stats["shared_with_kb"] is True
    assert stats["points_written"] == len(points)
    assert {p.payload["section_id"] for p in client.upserted} == set(EXPECTED_PAGES.values())


def test_ingest_never_creates_a_collection(points_and_stats, stubbed_embeddings, stubbed_model):
    """A second collection would also mean a second vector config, which is how the
    two halves of the corpus silently drift apart. _ensure_collection owns creation."""
    client = FakeClient()
    ingest_stressors(client=client)
    assert not hasattr(client, "create_collection")
    assert not hasattr(client, "created")


def test_ingest_does_not_touch_the_manual_collection(points_and_stats, stubbed_embeddings, stubbed_model):
    client = FakeClient()
    ingest_stressors(client=client)
    assert "barq_manual" not in (getattr(client, "upserted_name", None),)


def test_drop_removes_only_stressor_points(points_and_stats, stubbed_embeddings, stubbed_model):
    """The rollback has to spare the KB articles -- it is a filter, not a reindex."""
    client = FakeClient()
    ingest_stressors(client=client)
    dropped = drop_stressors(client=client)
    name, selector = client.deleted[-1]
    assert name == QDRANT.collection_name
    conditions = selector.filter.must
    assert [c.key for c in conditions] == ["is_stressor"]
    assert conditions[0].match.value is True
    assert dropped["dropped"] == len(client.upserted)
    assert dropped["collection"] == QDRANT.collection_name


def test_sync_kb_does_not_delete_stressor_points():
    """The regression this architecture invites: a stressor point carries an
    article_id and no content_hash, which is exactly what a ServiceNow article that
    disappeared looks like. sync_kb would delete all 48 of them."""
    stressor = {"article_id": "7.1", "content_hash": None, "is_stressor": True}
    assert stressor.get("content_hash") is None, "stressor points must carry no content_hash"

    client = FakeScrollClient([{"article_id": "KB0001", "content_hash": "h1"},
                               {"_is_marker": True},
                               stressor])
    stored = _stored_hashes(client)
    assert stored == {"KB0001": "h1"}, "a stressor point must not look like a KB article"
    assert "7.1" not in stored


def test_delete_article_cannot_reach_a_stressor_point():
    """must_not is_stressor, so a section label that collides with an article_id
    cannot take the extracted page down with it."""
    client = FakeDeleteClient()
    _delete_article(client, "7.1")
    filter_ = client.selector.filter
    assert [c.key for c in filter_.must] == ["article_id"]
    assert [c.key for c in filter_.must_not] == ["is_stressor"]


class FakeScrollClient:
    def __init__(self, points):
        self.points = points

    def scroll(self, name, limit=256, offset=None, with_payload=None, with_vectors=False):
        assert offset is None
        return ([type("P", (), {"payload": p})() for p in self.points], None)


class FakeDeleteClient:
    def delete(self, name, points_selector):
        self.name, self.selector = name, points_selector

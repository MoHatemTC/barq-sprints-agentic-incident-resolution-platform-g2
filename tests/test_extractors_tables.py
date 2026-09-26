"""Table extraction on the real manual plus hand-built pages that need it.

Two kinds of test live here.  The synthetic pages pin the geometry rules --
spanning headers, hairline slivers, wrapped rows, nested grids -- on documents
whose correct answer is not in doubt.  The manual tests assert only the facts
the manual actually contains, so a change in the PDF shows up as a failure
rather than being papered over.

Run: pytest tests/test_extractors_tables.py -v
"""
from __future__ import annotations

import io
from typing import Sequence

import pymupdf
import pytest

from src.retrieval.extractors.tables import (
    IMAGE_TRAITS,
    STRESSOR_SECTIONS,
    ExtractedTable,
    TableCell,
    TableExtractionResult,
    _attach_nested,
    extract_page_tables,
    extract_tables,
    merge_page_continuations,
    page_capability,
)

MANUAL = "data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def draw_grid(page: pymupdf.Page, rects: list[tuple[float, float, float, float]]) -> None:
    """Rule every edge of every cell, so find_tables sees a real table."""
    points: set[tuple[float, float]] = set()
    for x0, y0, x1, y1 in rects:
        points.update({(x0, y0), (x0, y1), (x1, y0), (x1, y1)})
    for x in {p[0] for p in points}:
        for a, b in zip(sorted(y for _, y in points if _ == x), sorted(y for _, y in points if _ == x)[1:]):
            page.draw_line((x, a), (x, b), width=0.6)
    for y in {p[1] for p in points}:
        xs = sorted(x for x, _ in points if _ == y)
        for a, b in zip(xs, xs[1:]):
            page.draw_line((a, y), (b, y), width=0.6)


def make_pdf(jobs: Sequence[tuple] | None = None) -> pymupdf.Document:
    """A PDF built from drawing jobs, then committed.

    A job is ``("page",)``, ``("grid", [cell rects])`` or ``("text", rect, text[, size[, bold]])``.
    The document is round-tripped because ``find_tables`` reads the page's
    committed content stream, and lines drawn into an unsaved page are not in it
    yet -- a table drawn and inspected in the same breath finds nothing.
    """
    doc = pymupdf.open()
    page = None
    for job in jobs or [("page",)]:
        if job[0] == "page":
            page = doc.new_page(width=595, height=842)
        elif job[0] == "grid":
            draw_grid(page, job[1])
        elif job[0] == "text":
            put(page, job[1], job[2], job[3] if len(job) > 3 else 7.0,
                job[4] if len(job) > 4 else False)
    return committed(doc)


def committed(doc: pymupdf.Document) -> pymupdf.Document:
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return pymupdf.open(stream=buffer.getvalue(), filetype="pdf")


def put(page: pymupdf.Page, rect: tuple[float, float, float, float], text: str,
        size: float = 7.0, bold: bool = False) -> None:
    page.insert_textbox(
        pymupdf.Rect(*rect), text, fontsize=size, fontname="hebo" if bold else "helv"
    )


def grid(x0: float, y0: float, cols: list[float], rows: list[float]) -> list[tuple[float, ...]]:
    return [
        (x0 + cols[i], y0 + rows[j], x0 + cols[i + 1], y0 + rows[j + 1])
        for j in range(len(rows) - 1)
        for i in range(len(cols) - 1)
    ]


def by_columns(tables: list[ExtractedTable], first: str) -> ExtractedTable:
    for table in tables:
        if table.columns and table.columns[0] == first:
            return table
    raise AssertionError(f"no table whose first column is {first!r}: "
                         f"{[t.columns for t in tables]}")


# --------------------------------------------------------------------------- #
# the manual: the assertions the PDF itself supports
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def manual_tables() -> TableExtractionResult:
    return extract_tables(pdf_path=MANUAL)


def test_merged_header_is_flattened_to_its_leaf_columns(manual_tables):
    """2.1 spans DUBAI and CAIRO over two columns each."""
    table = by_columns(manual_tables.tables, "COVERAGE")
    assert table.columns == [
        "COVERAGE",
        "DUBAI HOURS (GST)",
        "DUBAI ANALYSTS",
        "CAIRO HOURS (GST)",
        "CAIRO ANALYSTS",
    ]
    assert table.has_merged_header
    assert table.header_rows == 2
    working = table.as_records()[0]
    assert working == {
        "COVERAGE": "Working hours",
        "DUBAI HOURS (GST)": "08:00 – 18:00",
        "DUBAI ANALYSTS": "6",
        "CAIRO HOURS (GST)": "09:00 – 19:00",
        "CAIRO ANALYSTS": "5",
    }


def test_hairline_columns_are_dropped_and_reported(manual_tables):
    """find_tables reads the vertical hairlines as extra columns on 2.1."""
    table = by_columns(manual_tables.tables, "COVERAGE")
    assert table.raw_col_count > table.col_count
    assert any("sliver" in issue for issue in table.issues)


def test_nested_field_value_tables_attach_to_their_parent_cell(manual_tables):
    """5.2 puts a FIELD/VALUE grid inside the OWNERSHIP cell of every row."""
    outer = by_columns(manual_tables.tables, "SERVICE")
    assert outer.has_nested
    nested = outer.nested_tables()
    assert len(nested) == 5
    assert all(n.columns == ["FIELD", "VALUE"] for n in nested)
    assert nested[0].as_records() == [
        {"FIELD": "Owner", "VALUE": "K. Selim"},
        {"FIELD": "Group", "VALUE": "Platform Eng"},
        {"FIELD": "Window", "VALUE": "24/7"},
    ]
    # ...and the parent row reads the same facts inline, so a search over the
    # outer grid alone still finds the owner of a service.
    assert outer.as_records()[0]["OWNERSHIP AND WINDOW"] == (
        "Owner: K. Selim; Group: Platform Eng; Window: 24/7"
    )


def test_a_table_split_across_a_page_boundary_is_stitched(manual_tables):
    """7.1's journal runs off the foot of p25 and resumes at the top of p26."""
    journal = [t for t in manual_tables.tables if t.columns[:2] == ["TIME", "TYPE"]]
    assert journal, "the 7.1 journal is missing"
    for table in journal:
        if table.page_numbers == [25, 26]:
            assert table.spans_page_boundary
            entries = [row[0] for row in table.rows]
            assert entries[0] == "09:14" and entries[-1] == "10:02"
            assert any("continues onto page 26" in issue for issue in table.issues)
            break
    else:
        pytest.fail("no journal table was merged across pages 25 and 26")


def test_two_registers_are_not_merged_into_one(manual_tables):
    """8.3 keeps the problem register on p29 and the known-error register on p30.

    They are adjacent and share a page boundary, and merging them would produce
    a table whose columns mean two different things.
    """
    problem = by_columns(manual_tables.tables, "PROBLEM")
    known = by_columns(manual_tables.tables, "KNOWN ERROR")
    assert problem.page_numbers == [29]
    assert known.page_numbers == [30]
    assert problem.as_records()[0]["DESCRIPTION"].startswith("Repeat account lockouts")
    assert known.as_records()[0]["SYMPTOM"].startswith("SAP GUI times out")


def test_a_bordered_callout_is_not_reported_as_a_table(manual_tables):
    """The 2.1 aside about weekend cover is a box around prose, not a grid."""
    assert not [
        t for t in manual_tables.tables
        if t.page_numbers == [8] and any("Cairo covers Saturday" in c for row in t.rows for c in row)
    ]


def test_column_names_are_unique_so_records_cannot_lose_a_value(manual_tables):
    """A title spanning three columns would otherwise collapse into one key."""
    for table in manual_tables.tables:
        assert len(set(table.columns)) == len(table.columns), table.columns
        for record in table.as_records():
            assert len(record) == len(table.columns)


def test_provenance_records_the_stressor_run(manual_tables):
    prov = manual_tables.provenance
    assert prov["extractor"] == "pymupdf.find_tables"
    assert prov["is_stressor"] is True
    assert prov["pymupdf_version"]
    assert prov["table_count"] == len(manual_tables.tables)
    assert prov["nested_table_count"] == sum(len(t.nested_tables()) for t in manual_tables.tables)
    assert [25, 26] in prov["page_crossing_tables"]


def test_every_stressor_section_declares_its_classes():
    assert page_capability("5.2") == ["table", "nested_table"]
    assert page_capability("11.8") == ["ocr", "image"]
    assert page_capability("4.1") == []


def test_the_page_that_breaks_is_declared_on_7_1_and_not_on_8_3():
    """The contents page reads as though 8.3 is one long register; it is two, a
    problem register on p29 and a known-error register on p30, and the table that
    runs off the foot of a page is 7.1's journal.  Declaring page_crossing on 8.3
    would send the router looking for a continuation that is not there."""
    assert "page_crossing" in page_capability("7.1")
    assert "page_crossing" not in page_capability("8.3")
    assert "table" in page_capability("8.3")


def test_every_declared_class_is_one_the_extractors_can_produce():
    """A capability the extractors never emit is a test row that can never pass.

    This is not hypothetical: `dark_theme`, `rotated_image` and `arabic_rtl` were
    declared as capabilities until this test failed, and nothing in the codebase
    produces them.  They describe a picture, not an extraction, and now live in
    IMAGE_TRAITS.
    """
    produced = {"table", "nested_table", "merged_header", "page_crossing",
                "layout", "form_parsing", "key_value", "ocr", "checkbox", "image"}
    for section_id, classes in STRESSOR_SECTIONS.items():
        assert set(classes) <= produced, f"{section_id} declares {set(classes) - produced}"


def test_image_traits_are_traits_and_not_capabilities():
    traits = {t for v in IMAGE_TRAITS.values() for t in v}
    assert traits.isdisjoint(set().union(*STRESSOR_SECTIONS.values()))
    for section_id, why in IMAGE_TRAITS.items():
        assert "image" in STRESSOR_SECTIONS.get(section_id, []), \
            f"{section_id} has image traits but does not declare an image"


def test_page_capability_never_leaks_an_image_trait():
    """The two vocabularies are separate, so a caller reading the capability
    list cannot receive a word no extractor emits."""
    produced = {c for classes in STRESSOR_SECTIONS.values() for c in classes}
    for section_id in STRESSOR_SECTIONS:
        assert set(page_capability(section_id)) <= produced


# --------------------------------------------------------------------------- #
# synthetic pages: the geometry rules, pinned
# --------------------------------------------------------------------------- #


def test_wrapped_row_is_joined_onto_its_record():
    """A cell whose text continues into a band with no other cell is one row."""
    page = make_pdf([
        ("page",),
        ("grid", grid(40, 100, [0, 120, 300], [0, 30, 60, 90])),
        ("text", (45, 104, 155, 126), "KB0001", 7.0, True),
        ("text", (165, 104, 335, 126), "VPN authentication fails after a password"),
        ("text", (165, 133, 335, 156), "change. Clear the cached credential."),
    ])[0]
    table = extract_page_tables(page, 1)[0]
    assert table.row_count == 1
    assert "password change." in table.rows[0][1]


def test_a_spanning_header_does_not_lose_its_leaves():
    page = make_pdf([
        ("page",),
        ("grid", [
            (40, 100, 140, 130), (140, 100, 340, 130), (340, 100, 440, 130),
            (40, 130, 140, 155), (140, 130, 240, 155), (240, 130, 340, 155),
            (340, 130, 440, 155),
            (40, 155, 140, 180), (140, 155, 240, 180), (240, 155, 340, 180),
            (340, 155, 440, 180),
        ]),
        ("text", (45, 104, 135, 126), "SITE", 8.0, True),
        ("text", (145, 104, 335, 126), "DUBAI", 8.0, True),   # spans two columns
        ("text", (345, 104, 435, 126), "CAIRO", 8.0, True),
        ("text", (145, 133, 235, 152), "HOURS", 7.0, True),
        ("text", (245, 133, 335, 152), "ANALYSTS", 7.0, True),
        ("text", (345, 133, 435, 152), "HOURS", 7.0, True),
        ("text", (45, 158, 135, 177), "Working hours"),
        ("text", (145, 158, 235, 177), "08:00 to 18:00"),
        ("text", (245, 158, 335, 177), "6"),
        ("text", (345, 158, 435, 177), "09:00 to 19:00"),
    ])[0]

    table = extract_page_tables(page, 1)[0]
    assert table.has_merged_header
    assert table.col_count == 4
    # The spanning name and the leaf name are joined, so a record can be asked
    # for its hours without knowing which of the two headers carried the word.
    assert table.columns == ["SITE", "DUBAI HOURS", "DUBAI ANALYSTS", "CAIRO HOURS"]
    record = table.as_records()[0]
    assert record["DUBAI HOURS"] == "08:00 to 18:00"
    assert record["DUBAI ANALYSTS"] == "6"
    assert record["CAIRO HOURS"] == "09:00 to 19:00"


def test_a_nested_grid_is_attached_and_read_once():
    """The sub-grid's text is the parent's, so it must be read once, not twice.

    ``find_tables`` reports the sub-grid on its own *and* inside the parent's
    merged cell, so the child arrives with the very words the parent would
    otherwise have collected.  Attaching it must replace the parent's copy, not
    append a second one -- otherwise "K. Selim Platform Eng" is indexed twice
    and the parent's cell reads as a run-on.
    """
    parent = ExtractedTable(
        columns=["SERVICE", "OWNERSHIP AND WINDOW"],
        rows=[["order-processing", ""]],
        cells=[[TableCell(row=0, col=0, text="order-processing"),
                TableCell(row=0, col=1, text="")]],
        header_cells=[],
        bbox=(40, 100, 340, 160), page_numbers=[1],
        column_bands=[(40, 140), (140, 340)], row_bands=[(100, 130), (130, 160)],
    )
    child = ExtractedTable(
        columns=["FIELD", "VALUE"],
        rows=[["Owner", "K. Selim"], ["Group", "Platform Eng"]],
        cells=[[TableCell(row=0, col=0, text="Owner", is_header=True),
                TableCell(row=0, col=1, text="K. Selim", is_header=True)],
               [TableCell(row=1, col=0, text="Group"),
                TableCell(row=1, col=1, text="Platform Eng")]],
        header_cells=[[TableCell(row=0, col=0, text="Owner", is_header=True),
                       TableCell(row=0, col=1, text="K. Selim", is_header=True)]],
        bbox=(160, 106, 330, 128), page_numbers=[1],
    )
    _attach_nested([parent, child])

    assert parent.has_nested
    assert [n.bbox for n in parent.nested_tables()] == [child.bbox]
    assert parent.rows == [["order-processing", "Owner: K. Selim; Group: Platform Eng"]]
    assert parent.as_records()[0]["OWNERSHIP AND WINDOW"] == (
        "Owner: K. Selim; Group: Platform Eng"
    )
    assert child.as_records() == [
        {"FIELD": "Owner", "VALUE": "K. Selim"},
        {"FIELD": "Group", "VALUE": "Platform Eng"},
    ]


def test_a_repeated_nested_grid_is_attached_once():
    """find_tables can report the same sub-grid twice; the second copy is dropped."""
    parent = ExtractedTable(
        columns=["SERVICE", "OWNERSHIP AND WINDOW"],
        rows=[["order-processing", ""]],
        cells=[[TableCell(row=0, col=0, text="order-processing"),
                TableCell(row=0, col=1, text="")]],
        header_cells=[], bbox=(40, 100, 340, 160), page_numbers=[1],
        column_bands=[(40, 140), (140, 340)], row_bands=[(100, 160)],
    )
    one = ExtractedTable(
        columns=["FIELD", "VALUE"], rows=[["Owner", "K. Selim"]],
        cells=[[TableCell(row=0, col=0, text="Owner"), TableCell(row=0, col=1, text="K. Selim")]],
        header_cells=[], bbox=(160, 106, 330, 128), page_numbers=[1],
    )
    twin = ExtractedTable(
        columns=["FIELD", "VALUE"], rows=[["Owner", "K. Selim"]],
        cells=[[TableCell(row=0, col=0, text="Owner"), TableCell(row=0, col=1, text="K. Selim")]],
        header_cells=[], bbox=(160, 106, 330, 128), page_numbers=[1],
    )
    _attach_nested([parent, one, twin])
    assert len(parent.nested_tables()) == 1
    assert parent.rows[0][1] == "Owner: K. Selim"


def test_merging_refuses_two_tables_that_are_not_the_same_grid():
    left = ExtractedTable(
        columns=["TIME", "TYPE"], rows=[["09:14", "System"]], cells=[[]],
        header_cells=[], bbox=(0, 700, 500, 780), page_numbers=[1],
    )
    right = ExtractedTable(
        columns=["KNOWN ERROR", "SYMPTOM"], rows=[["KE0000034", "SAP GUI"]],
        cells=[[]], header_cells=[], bbox=(0, 40, 500, 200), page_numbers=[2],
    )
    merged = merge_page_continuations(
        {1: [left], 2: [right]}, {1: 842.0, 2: 842.0}
    )
    assert [t.page_numbers for t in merged] == [[1], [2]]


def test_a_cell_reaching_past_its_table_is_still_recorded():
    """Text that spills outside the ruled box is content, not a reason to lose it."""
    page = make_pdf([
        ("page",),
        ("grid", grid(40, 100, [0, 120, 300], [0, 30, 60])),
        ("text", (45, 104, 155, 126), "Service", 7.0, True),
        ("text", (165, 104, 335, 126), "Priority", 7.0, True),
        ("text", (45, 133, 155, 156), "corporate-vpn"),
        ("text", (165, 133, 335, 156), "P3 - Moderate"),
    ])[0]
    table = extract_page_tables(page, 1)[0]
    assert table.as_records()[0] == {"Service": "corporate-vpn", "Priority": "P3 - Moderate"}


def test_table_cell_covers_respects_both_spans():
    cell = TableCell(row=2, col=1, text="x", row_span=2, col_span=3)
    assert cell.covers(2, 1) and cell.covers(3, 3)
    assert not cell.covers(2, 0) and not cell.covers(4, 1)


def test_nested_table_values_are_indexed_once_and_only_once():
    """A sub-table's words must reach the index exactly once.

    ``_attach_nested`` renders each sub-table inline into its host cell's value
    (``TableCell.as_text``) and rebuilds the parent's rows, so ``to_markdown``
    already contains them. That is the only reason nested values are searchable
    at all -- so a second rendering appended to ``to_markdown`` would look like a
    fix and actually double-weight every sub-table value in both the dense and
    the sparse vector. This test is here to make that mistake loud.
    """
    parent = ExtractedTable(
        columns=["SERVICE", "OWNERSHIP AND WINDOW"],
        rows=[["order-processing", "Owner: K. Selim; Group: Platform Eng"]],
        cells=[[TableCell(row=0, col=0, text="order-processing"),
                TableCell(row=0, col=1, text="",
                          nested=[ExtractedTable(
                              columns=["FIELD", "VALUE"],
                              rows=[["Owner", "K. Selim"], ["Group", "Platform Eng"]],
                              cells=[[TableCell(row=0, col=0, text="Owner"),
                                      TableCell(row=0, col=1, text="K. Selim")],
                                     [TableCell(row=1, col=0, text="Group"),
                                      TableCell(row=1, col=1, text="Platform Eng")]],
                              header_cells=[], bbox=(160, 106, 330, 128), page_numbers=[1],
                          )])]],
        header_cells=[], bbox=(40, 100, 340, 160), page_numbers=[1],
    )
    markdown = parent.to_markdown()

    # present, because this is what the ingest path indexes
    assert "K. Selim" in markdown
    assert "Platform Eng" in markdown
    # present exactly once each: not lost, not duplicated
    assert markdown.count("K. Selim") == 1
    assert markdown.count("Platform Eng") == 1
    # and the grid itself is still a well-formed two-column table
    assert markdown.splitlines()[0] == "| SERVICE | OWNERSHIP AND WINDOW |"


def test_result_is_falsey_when_the_page_had_no_table():
    doc = make_pdf([("page",)])
    assert extract_page_tables(doc[0], 1) == []

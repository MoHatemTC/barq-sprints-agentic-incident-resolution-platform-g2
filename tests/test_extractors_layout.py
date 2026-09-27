"""Layout extraction: reading order, forms, and the OCR gate.

The manual gives the facts that matter -- 2-column pages, the incident form in
7.1, and the screenshots in 10.4 / 11.8 / 11.9 / 12.2.  The synthetic pages pin
the rules the manual cannot show, because the manual has no page with a
full-width banner splitting two columns, and no page with no text layer at all.

Run: pytest tests/test_extractors_layout.py -v
"""
from __future__ import annotations

import io

import pymupdf
import pytest

from src.retrieval.extractors.layout import (
    FormField,
    LayoutExtractionResult,
    TextBlock,
    detect_checkboxes,
    extract_form_fields,
    extract_layout,
    extract_layout_document,
    image_regions_needing_ocr,
    order_blocks,
    page_char_count,
    page_has_text_layer,
    page_needs_ocr,
)

MANUAL = "data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf"


@pytest.fixture(scope="module")
def manual() -> pymupdf.Document:
    return pymupdf.open(MANUAL)


def committed(doc: pymupdf.Document) -> pymupdf.Document:
    """Round-trip so drawn lines and inserted text reach the content stream."""
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return pymupdf.open(stream=buffer.getvalue(), filetype="pdf")


def text_page(runs: list[tuple[float, float, str]] | list[tuple[float, float, str, bool]],
              width: float = 595.0, height: float = 842.0) -> pymupdf.Document:
    """A page carrying nothing but the given runs, as committed content.

    A run is ``(x, y, text)`` or ``(x, y, text, bold)``; form keys are set bold
    because that is how the manual sets them, and a form key that is not bold is
    a sentence, not a key.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    for run in runs:
        x, y, text = run[:3]
        page.insert_text((x, y), text, fontsize=9,
                         fontname="hebo" if len(run) > 3 and run[3] else "helv")
    return committed(doc)


def block(index: int, bbox: tuple[float, ...], text: str = "x") -> TextBlock:
    return TextBlock(index=index, text=text, bbox=bbox, reading_order=index)


# --------------------------------------------------------------------------- #
# the manual
# --------------------------------------------------------------------------- #


def test_two_column_page_is_read_column_first(manual):
    """3.6 sets its material in two columns; reading it row-wise interleaves them."""
    result = extract_layout(manual[11], 12)
    assert result.column_count == 2
    assert result.provenance["reading_order_changed"] is True
    left_band, right_band = result.columns
    left = [b for b in result.blocks if b.column == 0 and not b.is_header_footer]
    right = [b for b in result.blocks if b.column == 1 and not b.is_header_footer]
    assert left and right
    centre = lambda b: (b.bbox[0] + b.bbox[2]) / 2
    assert all(left_band[0] <= centre(b) <= left_band[1] for b in left)
    assert all(right_band[0] <= centre(b) <= right_band[1] for b in right)
    assert right_band[0] - left_band[1] > 0, "the two bands must not overlap"
    # Every left-column block precedes every right-column block.
    assert max(b.reading_order for b in left) < min(b.reading_order for b in right)


def test_incident_form_is_read_as_label_value_pairs(manual):
    """7.1's record is a form: the label sits left of the value, on one line."""
    result = extract_layout(manual[24], 25)
    fields = result.field_map()
    assert fields["Number"] == "INC0010023"
    assert fields["Caller"] == "Mariam Fouad, Finance"
    assert fields["Impact / Urgency"] == "3 – Individual / 2 – Medium"
    assert fields["Article applied"] == "KB0001 v2"
    assert fields["Resolution code"] == "Resolved by knowledge article"
    assert all(f.key != "12" for f in result.form_fields), "the folio is not a field"
    assert all(f.key != "▪" for f in result.form_fields), "a bullet glyph is not a field"
    assert all(f.key != "KE0000034" for f in result.form_fields), "a record id is not a field"


def test_a_screenshot_is_routed_to_ocr_not_to_the_page(manual):
    """10.4's approval record is a raster: the page has text, the region does not.

    This is the distinction the whole OCR gate exists for.  The page carries the
    section heading and its commentary, so a page-level OCR would read it again
    and lose the vector text; the record inside the image has no text under it at
    all and is unreadable without OCR.
    """
    result = extract_layout(manual[34], 35)
    assert result.has_text_layer is True
    assert result.needs_ocr is False
    assert result.ocr_regions, "the approval record image was not flagged"
    region = result.ocr_regions[0]
    assert region["text_under"] == ""
    assert region["needs_ocr"] is True
    assert region["px"][0] > 0 and region["px"][1] > 0, "an OCR call needs a pixel size"


@pytest.mark.parametrize("page_number", [35, 42, 44])
def test_image_pages_are_identified_by_their_regions(manual, page_number):
    result = extract_layout(manual[page_number - 1], page_number)
    assert result.needs_ocr is False
    assert result.ocr_regions


def test_running_head_is_not_mistaken_for_body_text(manual):
    """A table at the foot of a page sits in the footer's band; it is content."""
    result = extract_layout(manual[24], 25)
    chrome = [b for b in result.blocks if b.is_header_footer]
    assert any("Service Operations Manual" in b.text for b in chrome)
    assert any("25 of 52" in b.text for b in chrome)
    journal = [b for b in result.blocks if "09:21" in b.text]
    assert journal, "the journal row at the foot of p25 was dropped as chrome"
    assert not journal[0].is_header_footer


def test_reading_order_is_recorded_and_reported(manual):
    result = extract_layout(manual[7], 8)
    assert [b.reading_order for b in result.blocks] == list(range(len(result.blocks)))
    assert result.provenance["extractor"] == "pymupdf.layout"
    assert result.provenance["is_stressor"] is True
    assert result.provenance["block_count"] == len(result.blocks)


def test_no_page_of_the_manual_needs_whole_page_ocr(manual):
    """Every page has a text layer, so a page-level OCR sweep would be waste."""
    results = extract_layout_document(manual)
    assert len(results) == manual.page_count
    assert [r.page_number for r in results] == list(range(1, manual.page_count + 1))
    assert not [r.page_number for r in results if r.needs_ocr]


# --------------------------------------------------------------------------- #
# synthetic pages
# --------------------------------------------------------------------------- #


def test_a_page_with_no_text_layer_is_sent_to_ocr():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(100, 200, 400, 400), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    assert page_char_count(page) == 0
    assert page_has_text_layer(page) is False
    assert page_needs_ocr(page) is True
    assert extract_layout(page, 1).needs_ocr is True


def test_a_full_width_banner_does_not_hide_the_columns_beneath_it():
    blocks = [
        block(0, (50, 60, 545, 80), "A banner across the whole page"),
        block(1, (50, 100, 280, 130), "left one"),
        block(2, (50, 140, 280, 170), "left two"),
        block(3, (320, 100, 545, 130), "right one"),
        block(4, (320, 140, 545, 170), "right two"),
    ]
    for b in blocks:
        b.column = 0 if b.bbox[0] < 300 else 1
    blocks[0].column = None
    ordered = [b.text for b in order_blocks(blocks, 842.0)]
    assert ordered == [
        "A banner across the whole page",
        "left one", "left two", "right one", "right two",
    ]


def test_a_single_column_page_is_read_top_to_bottom():
    blocks = [
        block(0, (50, 300, 545, 330), "third"),
        block(1, (50, 100, 545, 130), "first"),
        block(2, (50, 200, 545, 230), "second"),
    ]
    for b in blocks:
        b.column = 0
    assert [b.text for b in order_blocks(blocks, 842.0)] == ["first", "second", "third"]


def test_stacked_key_and_value_are_paired():
    page = text_page([
        (50, 100, "State", True),
        (50, 120, "published"),
        (50, 140, "Safety", True),
    ])[0]
    fields = {f.key: f for f in extract_form_fields(page)}
    assert fields["State"].value == "published"
    assert fields["State"].pattern == "stacked"


def test_a_row_of_column_headings_is_not_a_stacked_key():
    """``TIME TYPE ENTRY`` heads three columns; pairing it invents a field."""
    page = text_page([
        (50, 100, "TIME", True),
        (120, 100, "TYPE", True),
        (200, 100, "ENTRY", True),
        (50, 120, "09:14"),
        (120, 120, "System"),
    ])[0]
    assert [f.key for f in extract_form_fields(page)] == []


def test_a_bulleted_list_is_not_a_form():
    page = text_page([
        (50, 100, "▪", True),
        (70, 100, "What you checked and what you found", True),
        (50, 120, "▪", True),
        (70, 120, "Commands run and their output", True),
    ])[0]
    assert extract_form_fields(page) == []


def test_a_table_grid_is_not_read_as_a_checkbox():
    """Appendix D is nothing but small rectangles, and they are cell rules."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for y in (100, 130, 160):
        page.draw_line((40, y), (340, y), width=0.6)
    for x in (40, 190, 340):
        page.draw_line((x, 100), (x, 160), width=0.6)
    page = committed(doc)[0]
    assert detect_checkboxes(page) == []


def test_a_checkbox_outside_a_table_is_found_with_its_label():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(50, 100, 60, 110), width=0.6)
    page.insert_text((70, 108), "Escalate to the service owner", fontsize=9, fontname="helv")
    page = committed(doc)[0]
    found = detect_checkboxes(page)
    assert len(found) == 1
    assert found[0].label == "Escalate to the service owner"
    assert found[0].kind == "vector"
    assert found[0].checked is None, "a bare outline does not say whether it is ticked"


def test_a_letter_x_in_a_heading_is_not_a_tick():
    page = text_page([(50, 100, "N E X T  R E V I E W", True)])[0]
    assert detect_checkboxes(page) == []


def test_image_regions_skip_areas_that_carry_text():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(50, 100, 400, 300), pixmap=checkerboard())
    page.insert_text((50, 400), "outside the picture", fontsize=9, fontname="helv")
    regions = image_regions_needing_ocr(page)
    assert len(regions) == 1
    assert regions[0]["bbox"][1] < 300
    assert regions[0]["needs_ocr"] is True
    width, height = regions[0]["px"]
    assert width > 0 and height > 0, "an OCR call needs a pixel size to raster at"


def test_vector_art_is_not_reported_as_an_image():
    """A filled rectangle is a shape; only a raster needs OCR."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(50, 100, 400, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    assert image_regions_needing_ocr(committed(doc)[0]) == []


def checkerboard() -> pymupdf.Pixmap:
    return pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 7, 4), False)


def test_a_page_whose_only_content_is_a_raster_needs_ocr():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(50, 100, 400, 300), pixmap=checkerboard())
    result = extract_layout(committed(doc)[0], 1)
    assert result.has_text_layer is False
    assert result.needs_ocr is True
    assert result.ocr_regions


def test_result_is_falsey_for_a_blank_page():
    doc = pymupdf.open()
    assert not extract_layout(doc.new_page(width=595, height=842), 1)


def test_form_field_and_result_render_as_dicts():
    field = FormField(key="Number", value="INC0010023")
    assert field.as_dict() == {"key": "Number", "value": "INC0010023",
                               "checked": None, "pattern": "inline"}
    result = LayoutExtractionResult(
        text="x", blocks=[], column_count=1, columns=[(0.0, 1.0)],
        form_fields=[FormField(key="a", value="1"), FormField(key="b", value="2")],
        checkboxes=[], has_text_layer=True, needs_ocr=False, ocr_regions=[],
        page_number=1,
    )
    assert result.field_map() == {"a": "1", "b": "2"}
    assert bool(result) is True

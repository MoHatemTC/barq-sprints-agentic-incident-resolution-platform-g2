"""
S2.6 stressor extraction: reading order, form structure, and the OCR gate.

Three things ``page.get_text()`` gets wrong on the BARQ manual:

1. **Reading order.**  A page's internal text order is whatever the writer
   emitted, which for a two-column page interleaves the columns.  Section 3.6
   on page 12 puts all five "WORK NOTES" bullets before any "ADDITIONAL COMMENTS"
   bullet *and* splits one bullet's second line out of place, so a
   column-blind reader produces a document where the requester-facing bullets are
   attributed to the internal ones.  ``extract_layout`` rebuilds the order from
   the block bounding boxes: full-width blocks are emitted where they sit, and
   the blocks between them are sorted column-first, then top-to-bottom.

2. **Forms.**  An incident record is four label/value pairs per visual row, not
   prose.  ``extract_form_fields`` returns ``FormField`` records by treating a
   short bold span as the key and the plain spans to its right as its value, so
   ``Caller -> "Mariam Fouad, Finance"`` survives instead of being a fragment
   of a sentence.  Checkboxes are reported as ``checked`` state where the glyph
   says so, and as ``None`` where only a box outline is visible -- a form drawn
   as a raster image has neither, which is what the OCR gate is for.

3. **The OCR gate.**  ``page_needs_ocr`` reports whether a page has a text layer
   at all.  A page that does not is routed to ``ocr.py``; a page that does may
   still carry image regions with no text under them, and those are listed
   separately in ``ocr_regions`` so a caller can OCR a region rather than a
   whole page.

Provenance follows the ``OCRExtractionResult`` pattern in ``ocr.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import pymupdf

from .geometry import containment

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "pymupdf.layout"

# A page with fewer than this many non-whitespace characters is treated as having
# no text layer.  The manual's sparsest real page is ~900 characters, so this is
# a wide margin that only trips on genuinely image-only pages.
MIN_PAGE_CHARS = 20
# Blocks narrower than this share of the content width never constrain the column
# structure: a full-width paragraph spans every gutter and would hide it.
FULL_WIDTH_FRACTION = 0.6
# The thinnest vertical gap that counts as a gutter between two columns.
MIN_GUTTER_PT = 16.0
# Each surviving column must contain at least this many blocks, otherwise a
# stray one-word block splits a single column into two.
MIN_BLOCKS_PER_COLUMN = 2
# Header/footer bands, as a fraction of page height.
HEADER_BAND = 0.075
FOOTER_BAND = 0.93
# A running head or folio is set smaller than body text.  Body copy that merely
# happens to sit near a margin (a table row at the foot of a page, a numbered
# list at the top of one) must not be mistaken for chrome, so a geometric hit
# only counts when the type is also small.
RUNNING_HEAD_MAX_PT = 8.5
# A running head is two lines of chrome.  The manual sets its table body at 8pt,
# the same size as the running head, so size alone cannot tell them apart: a
# journal at the foot of a page is five lines of 8pt and is content.
MAX_CHROME_CHARS = 120
PAGE_MARKER = re.compile(r"^\s*\d{1,3}\s+of\s+\d{1,3}\b")
DOCUMENT_TITLE = re.compile(
    r"service\s+operations\s+manual|internal\s+document|edition\s+\d", re.IGNORECASE
)
# Form labels are small bold runs; section headings are 10pt+ and would
# otherwise be read as a key with the paragraph below as its value.
MAX_FORM_LABEL_PT = 9.0
MAX_FORM_LABEL_WORDS = 4
# A form key starts with a capital and reads like a name ("Impact / Urgency",
# "Article applied"), never like a bullet glyph, a page number, a record id
# (KE0000034) or a stray fragment of a sentence that happens to be bold.
FIELD_KEY = re.compile(r"^[A-Z][A-Za-z0-9 /&()#.'’\-]{0,44}$")
RECORD_ID = re.compile(r"^[A-Z]{1,5}[-_ ]?\d{3,}[A-Za-z0-9]*$")
# Box-like vector art, in points.
CHECKBOX_MIN_PT = 5.0
CHECKBOX_MAX_PT = 18.0
MAX_CHECKBOX_LABEL_WORDS = 6

# Only real ballot marks.  A letter "X" is a letter: the manual decorates its
# headings with them ("N E X T  R E V I E W", "**RETIRED**") and reading those as
# ticks turns every section heading into a ticked checkbox.
CHECKED_GLYPHS = {"☑", "☒", "✓", "✔", "☓", "√"}
UNCHECKED_GLYPHS = {"☐", "□", "▢"}
BOLD_FLAG = 16


def _clean(text: str | None) -> str:
    # Keeps newlines, unlike ``tables._clean`` which collapses them.  Deliberate:
    # ``_block_text`` rebuilds a block line by line, so flattening a line here
    # would throw away the reading order this module exists to recover.  Do not
    # merge the two -- see ``tables._clean`` for the other half of why.
    return re.sub(r"[ \t]+", " ", (text or "")).strip()


def _line_text(line: dict) -> str:
    return _clean("".join(span["text"] for span in line.get("spans", [])))


def _block_text(block: dict) -> str:
    return "\n".join(_line_text(line) for line in block.get("lines", []) if _line_text(line)).strip()


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


@dataclass
class TextBlock:
    """A text block with its reading position resolved."""

    index: int                       # position in the PDF's own internal order
    text: str
    bbox: tuple[float, float, float, float]
    reading_order: int
    column: int | None = None       # None for a full-width block
    is_header_footer: bool = False
    font_size: float = 0.0
    is_bold: bool = False


@dataclass
class FormField:
    """One label/value pair.  ``checked`` is None unless the source is a checkbox."""

    key: str
    value: str
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    checked: bool | None = None
    pattern: str = "inline"          # "inline" | "stacked"

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "checked": self.checked, "pattern": self.pattern}


@dataclass
class Checkbox:
    label: str
    checked: bool | None
    bbox: tuple[float, float, float, float]
    kind: str = "glyph"              # "glyph" | "vector"

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "checked": self.checked, "kind": self.kind}


@dataclass
class LayoutExtractionResult:
    """Mirrors ``OCRExtractionResult``: payload plus provenance."""

    text: str
    blocks: list[TextBlock]
    column_count: int
    columns: list[tuple[float, float]]
    form_fields: list[FormField]
    checkboxes: list[Checkbox]
    has_text_layer: bool
    needs_ocr: bool
    ocr_regions: list[dict[str, Any]]
    page_number: int
    provenance: dict[str, Any] = field(default_factory=dict)

    def field_map(self) -> dict[str, str]:
        return {f.key: f.value for f in self.form_fields}

    def __bool__(self) -> bool:
        return bool(self.text)


# --------------------------------------------------------------------------- #
# text layer / OCR gate
# --------------------------------------------------------------------------- #


def page_char_count(page: pymupdf.Page) -> int:
    return len(re.sub(r"\s+", "", page.get_text() or ""))


def page_has_text_layer(page: pymupdf.Page, min_chars: int = MIN_PAGE_CHARS) -> bool:
    """True when the page carries a real text layer, false for a pure scan."""
    return page_char_count(page) >= min_chars


def page_needs_ocr(page: pymupdf.Page, min_chars: int = MIN_PAGE_CHARS) -> bool:
    """True when the page cannot be read without OCR."""
    return not page_has_text_layer(page, min_chars)


def image_regions_needing_ocr(
    page: pymupdf.Page, min_chars: int = 8, min_side: float = 40.0
) -> list[dict[str, Any]]:
    """Image areas on a page with no extractable text under them.

    A page can have a perfectly good text layer and still hide a screenshot, a
    scanned form or a rotated payload inside a picture.  Those regions are what
    a caller should hand to ``ocr.py``, not the whole page.
    """
    regions: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = tuple(float(v) for v in block["bbox"])
        if min(bbox[2] - bbox[0], bbox[3] - bbox[1]) < min_side:
            continue
        under = re.sub(r"\s+", "", page.get_text("text", clip=pymupdf.Rect(bbox)))
        if len(under) >= min_chars:
            continue
        regions.append({
            "bbox": bbox,
            "px": (int(block.get("width", 0)), int(block.get("height", 0))),
            "text_under": under,
            "needs_ocr": True,
        })
    return regions


# --------------------------------------------------------------------------- #
# columns
# --------------------------------------------------------------------------- #


def _content_bounds(blocks: Sequence[TextBlock]) -> tuple[float, float]:
    xs0 = [b.bbox[0] for b in blocks]
    xs1 = [b.bbox[2] for b in blocks]
    return (min(xs0), max(xs1)) if xs0 else (0.0, 0.0)


def detect_columns(
    blocks: Sequence[TextBlock], page_width: float
) -> list[tuple[float, float]]:
    """Column bands from a vertical-whitespace projection of the block boxes.

    Blocks that span most of the content width are excluded from the projection:
    a full-width paragraph covers every gutter on the page and would otherwise
    hide the column structure completely.  What is left has to leave a gap of at
    least ``MIN_GUTTER_PT`` for a gutter to exist, and each surviving band needs
    at least ``MIN_BLOCKS_PER_COLUMN`` blocks so a stray one-word block in a
    margin cannot invent a column.
    """
    body = [b for b in blocks if not b.is_header_footer]
    if not body:
        return []
    left, right = _content_bounds(body)
    content_width = max(right - left, 1e-6)

    narrow = [b for b in body if (b.bbox[2] - b.bbox[0]) < FULL_WIDTH_FRACTION * content_width]
    if len(narrow) < MIN_BLOCKS_PER_COLUMN:
        return [(left, right)]

    edges = sorted((b.bbox[0], b.bbox[2]) for b in narrow)
    merged: list[list[float]] = [list(edges[0])]
    for x0, x1 in edges[1:]:
        if x0 <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])

    bands = [(a, b) for a, b in merged if b - a >= MIN_GUTTER_PT]
    if len(bands) < 2:
        return [(left, right)]
    counts = [
        sum(1 for b in narrow if band[0] - 1.0 <= (b.bbox[0] + b.bbox[2]) / 2 <= band[1] + 1.0)
        for band in bands
    ]
    if any(c < MIN_BLOCKS_PER_COLUMN for c in counts):
        return [(left, right)]
    del page_width
    return bands


def _column_of(bbox: Sequence[float], bands: Sequence[tuple[float, float]]) -> int | None:
    if len(bands) < 2:
        return 0
    centre = (bbox[0] + bbox[2]) / 2
    for i, (x0, x1) in enumerate(bands):
        if x0 - 1.0 <= centre <= x1 + 1.0:
            return i
    return min(range(len(bands)), key=lambda i: abs(centre - sum(bands[i]) / 2))


# --------------------------------------------------------------------------- #
# reading order
# --------------------------------------------------------------------------- #


def _is_header_footer(
    bbox: Sequence[float], page_height: float, text: str = "", font_size: float = 0.0
) -> bool:
    """Running head or folio, judged on what the text is as well as where it is.

    Position alone is not enough.  A journal table at the foot of a page and a
    numbered procedure at the head of one sit in the same bands as the chrome
    and would be dropped along with it, losing the very content the page is
    about.  So a block in a band only counts as chrome when it is set like
    chrome (small type) or reads like it (the document title, or a folio).
    """
    if PAGE_MARKER.match(text) or DOCUMENT_TITLE.search(text):
        return len(text) <= MAX_CHROME_CHARS
    height = bbox[3] - bbox[1]
    if height >= 0.5 * page_height or len(text) > MAX_CHROME_CHARS:
        return False
    if font_size and font_size > RUNNING_HEAD_MAX_PT:
        return False
    return bbox[1] <= page_height * HEADER_BAND or bbox[3] >= page_height * FOOTER_BAND


def order_blocks(
    blocks: Sequence[TextBlock], page_height: float
) -> list[TextBlock]:
    """Column-first, top-to-bottom reading order for a page's blocks.

    Full-width blocks keep their vertical position and act as separators: the
    blocks above one are ordered, then the separator, then the blocks below it
    are ordered again.  That is what keeps a running head or a two-column banner
    on the right line while the columns beneath it are read left-then-right
    instead of being interleaved.
    """
    if not blocks:
        return []
    body = [b for b in blocks if not b.is_header_footer]
    headers = [b for b in blocks if b.is_header_footer]

    if all(b.column is None or b.column == 0 for b in body):
        ordered = sorted(body, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))
    else:
        separators = sorted((b for b in body if b.column is None), key=lambda b: b.bbox[1])
        buckets: list[list[TextBlock]] = [[]]
        edges = [b.bbox[1] for b in separators]
        for block in sorted((b for b in body if b.column is not None), key=lambda b: b.bbox[1]):
            slot = sum(1 for edge in edges if edge <= block.bbox[1] + 0.5)
            while len(buckets) <= slot:
                buckets.append([])
            buckets[slot].append(block)
        ordered = []
        for i, bucket in enumerate(buckets):
            ordered.extend(sorted(bucket, key=lambda b: (b.column or 0, round(b.bbox[1], 1), b.bbox[0])))
            if i < len(separators):
                ordered.append(separators[i])

    out: list[TextBlock] = []
    for block in headers + ordered:
        block.reading_order = len(out)
        out.append(block)
    return out


# --------------------------------------------------------------------------- #
# forms
# --------------------------------------------------------------------------- #


@dataclass
class _Run:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool


def _runs(page: pymupdf.Page) -> list[list[_Run]]:
    """Spans regrouped into visual rows, left to right within each row."""
    rows: list[list[_Run]] = []
    data = page.get_text("dict")
    spans: list[dict] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if _clean(span["text"]):
                    spans.append(span)
    spans.sort(key=lambda s: (round(s["bbox"][1] / 4.0), s["bbox"][0]))

    def top(s: dict) -> float:
        return s["bbox"][1]

    current: list[dict] = []
    current_top = None
    for span in spans:
        if current_top is None or abs(top(span) - current_top) <= 4.0:
            current.append(span)
            current_top = top(span) if current_top is None else current_top
        else:
            rows.append(_to_runs(current))
            current = [span]
            current_top = top(span)
    if current:
        rows.append(_to_runs(current))
    return rows


def _to_runs(spans: Sequence[dict]) -> list[_Run]:
    runs: list[_Run] = []
    for span in sorted(spans, key=lambda s: s["bbox"][0]):
        text = _clean(span["text"])
        if not text:
            continue
        if runs and abs(runs[-1].size - span["size"]) < 0.2 and (runs[-1].bold == bool(span["flags"] & BOLD_FLAG)):
            runs[-1].text = _clean(f"{runs[-1].text} {text}")
            runs[-1].bbox = (
                runs[-1].bbox[0], min(runs[-1].bbox[1], span["bbox"][1]),
                max(runs[-1].bbox[2], span["bbox"][2]), max(runs[-1].bbox[3], span["bbox"][3]),
            )
        else:
            runs.append(_Run(text, tuple(span["bbox"]), span["size"], bool(span["flags"] & BOLD_FLAG)))
    return runs


def extract_form_fields(page: pymupdf.Page) -> list[FormField]:
    """Label/value pairs, as an incident record or approval form actually reads.

    Two patterns are recognised, both from the manual:

    * ``inline``  -- ``Caller   Mariam Fouad, Finance   Channel   Self-service portal``
      is two fields; the short bold run is the key, everything up to the next
      bold run is its value.
    * ``stacked`` -- a KB header block prints ``State`` on one line and
      ``published`` on the next.
    """
    fields: list[FormField] = []
    rows = _runs(page)
    for i, row in enumerate(rows):
        keys = [j for j, run in enumerate(row) if _is_label_span(run)]
        if not keys:
            continue
        for pos, j in enumerate(keys):
            end = keys[pos + 1] if pos + 1 < len(keys) else len(row)
            value = _clean(" ".join(r.text for r in row[j + 1:end]))
            if value:
                fields.append(FormField(row[j].text, value, row[j].bbox, pattern="inline"))
                continue
            if j != 0:
                continue
            if _looks_like_table_header(row[j].text):
                continue
            nxt = rows[i + 1] if i + 1 < len(rows) else []
            nxt = [r for r in nxt if not _is_label_span(r)]
            if nxt and abs(nxt[0].bbox[0] - row[j].bbox[0]) <= 6.0:
                fields.append(FormField(row[j].text, nxt[0].text, nxt[0].bbox, pattern="stacked"))
    return fields


def _looks_like_table_header(text: str) -> bool:
    """``TIME TYPE ENTRY`` is a header row over three columns, not a form key.

    A stacked key is one key.  When the bold run is two or more all-caps words
    side by side, the text belongs to a row of column headings, and pairing it
    with the row underneath would invent a field the form never had.
    """
    return len(re.findall(r"\b[A-Z][A-Z0-9/&-]*\b", text)) >= 2


def _is_label_span(run: _Run) -> bool:
    """A short, bold, label-shaped run sitting to the left of a value.

    Boldness alone is not enough.  A bulleted list sets its bullet glyph in the
    same bold face as a form label, and ``12  of 52`` sets a folio the same way,
    so the text has to look like a key: it starts with a letter and is short.
    """
    if not run.bold or run.size > MAX_FORM_LABEL_PT:
        return False
    words = run.text.split()
    if len(words) > MAX_FORM_LABEL_WORDS:
        return False
    if RECORD_ID.match(run.text):
        return False
    return bool(FIELD_KEY.match(run.text))


def detect_checkboxes(
    page: pymupdf.Page, exclude_rects: Sequence[Sequence[float]] | None = None
) -> list[Checkbox]:
    """Checkboxes, from the glyph if one is drawn and from the vector box otherwise.

    ``checked`` is ``None`` for a bare box outline: an empty box and a box whose
    tick was lost to a raster downsample are not the same claim, and guessing
    would be worse than saying so.

    Both detectors have to be told apart from things that only look like a
    checkbox.  A decorative ``X`` inside a word is a letter, not a tick, so the
    glyph set holds only real ballot marks and the glyph must stand alone in its
    own span.  A small rectangle is only a checkbox if it is not part of a
    table's grid: the directory in Appendix D is nothing but small rectangles
    and cell rules, and reading those as checkboxes would invent a form.
    """
    found: list[Checkbox] = []
    for row in _runs(page):
        for run in row:
            for match in re.finditer("[" + re.escape("".join(CHECKED_GLYPHS | UNCHECKED_GLYPHS)) + "]", run.text):
                if match.start() and run.text[match.start() - 1].isalnum():
                    continue                     # part of a word, not a mark
                label = _clean(run.text[match.end():])
                if not label or len(label.split()) > MAX_CHECKBOX_LABEL_WORDS:
                    continue
                found.append(Checkbox(
                    label=label,
                    checked=True if run.text[match.start()] in CHECKED_GLYPHS else False,
                    bbox=(run.bbox[0], run.bbox[1], run.bbox[2], run.bbox[3]),
                    kind="glyph",
                ))

    if exclude_rects is None:
        try:
            exclude_rects = [t.bbox for t in page.find_tables(strategy="lines").tables]
        except Exception:  # a table sweep must never lose the checkboxes
            logger.debug("checkbox detection could not read the table grid", exc_info=True)
            exclude_rects = []

    labels = [run for row in _runs(page) for run in row]
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None or len(drawing.get("items", ())) != 1:
            continue
        w, h = rect.width, rect.height
        if not (CHECKBOX_MIN_PT <= w <= CHECKBOX_MAX_PT and CHECKBOX_MIN_PT <= h <= CHECKBOX_MAX_PT):
            continue
        if abs(w - h) > 0.35 * max(w, h):
            continue
        if any(containment((rect.x0, rect.y0, rect.x1, rect.y1), box) > 0.5 for box in exclude_rects):
            continue
        found.append(Checkbox(
            label=_label_right_of(rect, labels),
            checked=None,
            bbox=(rect.x0, rect.y0, rect.x1, rect.y1), kind="vector",
        ))
    return found


def _label_right_of(rect: Any, runs: Sequence[_Run]) -> str:
    """The short text starting to the right of a box, on the same line."""
    best = ""
    best_start = 0.0
    for run in runs:
        if not (rect.y0 - 2.0 <= run.bbox[1] and run.bbox[3] <= rect.y1 + 4.0):
            continue
        if run.bbox[0] < rect.x1 - 1.0:
            continue
        text = _clean(run.text)
        if text and (not best or run.bbox[0] < best_start):
            best, best_start = text, run.bbox[0]
    return best if len(best.split()) <= MAX_CHECKBOX_LABEL_WORDS else ""


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def extract_layout(
    page: pymupdf.Page, page_number: int | None = None
) -> LayoutExtractionResult:
    """Read one page in column order, with its form structure and OCR verdict."""
    number = page.number + 1 if page_number is None else page_number
    page_height = page.rect.height

    raw: list[TextBlock] = []
    for index, block in enumerate(page.get_text("dict").get("blocks", [])):
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        if not text:
            continue
        bbox = tuple(float(v) for v in block["bbox"])
        sizes, bold = _block_font(block)
        raw.append(TextBlock(
            index=index, text=text, bbox=bbox, reading_order=index,
            is_header_footer=_is_header_footer(bbox, page_height, text, sizes),
            font_size=sizes, is_bold=bold,
        ))

    bands = detect_columns(raw, page.rect.width)
    for block in raw:
        block.column = _column_of(block.bbox, bands)

    ordered = order_blocks(raw, page_height)
    text = "\n\n".join(b.text for b in ordered)
    has_layer = page_has_text_layer(page)
    regions = image_regions_needing_ocr(page)
    fields = extract_form_fields(page)

    provenance = {
        "extractor": EXTRACTOR_NAME,
        "pymupdf_version": getattr(pymupdf, "VersionBind", "unknown"),
        "method": "bbox-column-sort" if len(bands) > 1 else "single-column-y-sort",
        "page_number": number,
        "block_count": len(raw),
        "column_count": len(bands),
        "columns": [tuple(round(v, 1) for v in b) for b in bands],
        "reading_order_changed": [b.index for b in ordered] != sorted(b.index for b in raw),
        "form_field_count": len(fields),
        "checkbox_count": len(detect_checkboxes(page)),
        "has_text_layer": has_layer,
        "needs_ocr": not has_layer,
        "ocr_region_count": len(regions),
        "is_stressor": True,
    }
    return LayoutExtractionResult(
        text=text,
        blocks=ordered,
        column_count=len(bands),
        columns=bands,
        form_fields=fields,
        checkboxes=detect_checkboxes(page),
        has_text_layer=has_layer,
        needs_ocr=not has_layer,
        ocr_regions=regions,
        page_number=number,
        provenance=provenance,
    )


def _block_font(block: dict) -> tuple[float, bool]:
    sizes, bold = [], False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if _clean(span["text"]):
                sizes.append(span["size"])
                bold = bold or bool(span["flags"] & BOLD_FLAG)
    return (max(sizes) if sizes else 0.0), bold


def extract_layout_document(
    doc: pymupdf.Document, page_numbers: Iterable[int] | None = None
) -> list[LayoutExtractionResult]:
    """``extract_layout`` over a set of pages, in page order."""
    wanted = sorted(set(page_numbers)) if page_numbers is not None else list(range(1, doc.page_count + 1))
    return [
        extract_layout(doc[n - 1], n)
        for n in wanted
        if 1 <= n <= doc.page_count
    ]


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import sys

    from ...config import RETRIEVAL

    pages = [int(a) for a in sys.argv[1:]] or [1]
    for result in extract_layout_document(pymupdf.open(RETRIEVAL.manual_pdf_path), pages):
        print(f"=== page {result.page_number}: {result.column_count} column(s), "
              f"needs_ocr={result.needs_ocr}, fields={len(result.form_fields)}")
        print(result.text[:1200])
        for field_ in result.form_fields:
            print("   ", field_.as_dict())

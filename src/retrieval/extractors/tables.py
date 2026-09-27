"""
S2.6 stressor extraction: tables that ``page.find_tables()`` gets structurally wrong.

PyMuPDF's ``find_tables()`` returns raw cell rectangles. That is the right
starting point and the right place to get geometry from, but on the BARQ manual
it is the wrong answer, in three specific ways:

1. **Hairline "sliver" columns.**  The manual's tables are drawn with a thin
   double border, so the detector also reports 4-6pt columns that exist only
   where two border strokes overlap.  Section 2.1's coverage table comes back as
   14 columns; it has 5.
2. **Merged / spanning header cells.**  A real ``colspan`` (DUBAI over both the
   Dubai hours and the Dubai analyst count) is reported as ``None`` for the
   covered columns, so a naive ``extract()`` yields ``['', 'COVERAGE', '',
   '', 'DUBAI', ...]`` -- a header nobody can use.
3. **Nested tables.**  The FIELD/VALUE sub-grid inside a catalogue cell is
   reported as a separate top-level table, so the parent cell looks like an
   unparseable run-on string and the child is orphaned.

On top of that, a table can be cut off at the foot of a page and resumed at the
head of the next one (7.1's journal, 9.3's timeline), which must come back as
one logical table rather than two fragments.

The reconstruction here, in order:

* take the raw cell grid from ``find_tables()``;
* derive the true column bands from the most completely tiled row and drop the
  slivers;
* map every cell (merged ones included) onto the surviving bands by x-overlap,
  keeping its true ``col_span``/``row_span``;
* drop the all-empty bands the hairline grid injects, and re-join rows that are
  really one wrapped cell;
* treat the leading bold band as the header and flatten it into one column-name
  list, so "DUBAI" + "HOURS (GST)" becomes "DUBAI HOURS (GST)";
* attach any table that lives inside another table's cell to that cell, and
  collapse the duplicates the detector produces for the same sub-grid;
* stitch a bottom-of-page fragment to its top-of-next-page continuation,
  dropping the repeated header row.

``confidence`` is cell-occupancy: the share of the reconstructed logical grid
that actually carries text or a nested table.  It is a completeness measure,
not a claim that the grid is right -- ``issues`` records what had to be
repaired, because on this corpus something always has to be.

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

EXTRACTOR_NAME = "pymupdf.find_tables"

# A band narrower than this is a border artefact, not a column.  Real content
# columns in the manual are >= 40pt; the hairline slivers are 4-6pt.
SLIVER_WIDTH_PT = 8.0
# A cell belongs to a band when it covers at least this fraction of the band's
# width.  0.5 makes a cell that straddles two bands (a merged header) belong to
# both, while a cell that merely abuts a neighbour does not.
COLUMN_OVERLAP_RATIO = 0.5
# A leading row counts as header when this share of its non-empty cells is bold.
BOLD_HEADER_RATIO = 0.6
# How close to the physical page edge a table has to be to count as cut off.
BOTTOM_MARGIN_FRACTION = 0.92
TOP_MARGIN_FRACTION = 0.12
# Allowed drift between the column bands of a fragment and its continuation,
# as a fraction of the table width.
COLUMN_BAND_TOLERANCE = 0.08


def _clean(text: str | None) -> str:
    # Collapses newlines too, unlike ``layout._clean`` which keeps them.  That is
    # deliberate: a cell's value belongs on one line of a markdown row, whereas
    # layout assembles blocks line by line and needs the line breaks to survive.
    # Do not merge the two -- see ``layout._clean`` for the other half of why.
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


@dataclass
class TableCell:
    """One logical cell.  ``row``/``col`` are the top-left corner in the logical grid."""

    row: int
    col: int
    text: str
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    nested: list["ExtractedTable"] = field(default_factory=list)

    def as_text(self) -> str:
        """The cell's readable value, rendering any nested sub-tables inline."""
        if not self.nested:
            return self.text
        parts = [self.text] if self.text else []
        for nested in self.nested:
            pairs = [
                f"{record.get(nested.columns[0], '')}: {record.get(nested.columns[1], '')}".strip(": ")
                if len(nested.columns) >= 2 else ", ".join(v for v in record.values() if v)
                for record in nested.as_records()
            ]
            parts.append("; ".join(p for p in pairs if p))
        return " — ".join(p for p in parts if p)

    def covers(self, row: int, col: int) -> bool:
        return self.row <= row < self.row + self.row_span and self.col <= col < self.col + self.col_span


@dataclass
class ExtractedTable:
    """A table as structured rows and columns, plus what it took to get there."""

    columns: list[str]
    rows: list[list[str]]
    cells: list[list[TableCell]]
    header_cells: list[list[TableCell]]
    bbox: tuple[float, float, float, float]
    page_numbers: list[int]
    column_bands: list[tuple[float, float]] = field(default_factory=list)
    row_bands: list[tuple[float, float]] = field(default_factory=list)
    header_rows: int = 0
    raw_row_count: int = 0
    raw_col_count: int = 0
    has_merged_header: bool = False
    has_nested: bool = False
    spans_page_boundary: bool = False
    continued_from: int | None = None
    method: str = EXTRACTOR_NAME
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)

    # -- convenience ------------------------------------------------------- #

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def col_count(self) -> int:
        return len(self.columns)

    @property
    def page_number(self) -> int:
        return self.page_numbers[0] if self.page_numbers else 0

    def cell(self, row: int, col: int) -> TableCell | None:
        """The cell anchored at ``(row, col)``; shadowed span cells return None."""
        if not 0 <= row < len(self.cells):
            return None
        for candidate in self.cells[row]:
            if candidate.col == col:
                return candidate
        return None

    def as_records(self) -> list[dict[str, str]]:
        """Rows as ``{column: value}`` dicts, which is what gets indexed."""
        return [dict(zip(self.columns, row)) for row in self.rows]

    def nested_tables(self) -> list["ExtractedTable"]:
        found: list[ExtractedTable] = []
        for row in self.cells + self.header_cells:
            for cell in row:
                found.extend(cell.nested)
        return found

    def to_markdown(self) -> str:
        """The grid as markdown, which is what gets indexed.

        Nested sub-tables are already in here, rendered inline into their host
        cell's value by ``_attach_nested`` (see ``TableCell.as_text``), which
        rebuilds the parent's rows once the cells carry their sub-tables.  They
        must not be appended again: the same value would then appear two or
        three times in one point's text, weighting it further in both the dense
        and the sparse vector for no gain.
        """
        def line(cells: Sequence[str]) -> str:
            return "| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |"

        out = [line(self.columns), "|" + "---|" * len(self.columns)]
        out += [line(r) for r in self.rows]
        return "\n".join(out)

    def to_text(self) -> str:
        """Flat rendering for indexing; the header row is included."""
        return self.to_markdown()


@dataclass
class TableExtractionResult:
    """Mirrors ``OCRExtractionResult``: payload plus provenance."""

    tables: list[ExtractedTable]
    page_numbers: list[int]
    provenance: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.tables)

    def __len__(self) -> int:
        return len(self.tables)

    def __iter__(self):
        return iter(self.tables)


@dataclass
class _LogicalRow:
    y0: float
    y1: float
    cells: list[TableCell]


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #


def _reference_bands(
    rows: Sequence[Any],
    tolerance: float = 1.0,
    min_support: int = 2,
    min_ratio: float = 0.15,
) -> list[tuple[float, float]]:
    """The table's true column bands, found by voting on where its edges are.

    A real column separator is an edge that many rows agree on.  No single row
    can be trusted to show the whole grid: a form-style table puts the label of
    one field in the row above the value of another, so the row with the most
    cells is only half the story, and comparing rows by position pairs up cells
    that never shared a row.  Instead every cell edge across the table votes,
    edges within ``tolerance`` of one another are pooled, and only the edges with
    enough support become cuts.  The intervals between consecutive cuts are the
    bands.
    """
    grid = [c for row in rows for c in row.cells if c is not None]
    if not grid:
        return []
    left = min(c[0] for c in grid)
    right = max(c[2] for c in grid)
    if right - left <= 0:
        return []
    live_rows = [row for row in rows if any(c is not None for c in row.cells)]
    needed = max(min_support, int(min_ratio * len(live_rows)))

    votes: dict[int, list[tuple[int, float]]] = {}
    for row_index, row in enumerate(rows):
        for cell in row.cells:
            if cell is None:
                continue
            for x in (cell[0], cell[2]):
                votes.setdefault(int(round(x / tolerance)), []).append((row_index, x))

    cuts: list[float] = [left]
    for bucket in sorted(votes):
        pooled = votes[bucket]
        x = sum(x for _, x in pooled) / len(pooled)
        if left + tolerance < x < right - tolerance:
            if len({row_index for row_index, _ in pooled}) >= needed:
                if x - cuts[-1] > tolerance:
                    cuts.append(x)
    cuts.append(right)

    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


def _drop_slivers(
    bands: Sequence[tuple[float, float]], sliver_width_pt: float
) -> tuple[list[tuple[float, float]], int]:
    kept = [b for b in bands if (b[1] - b[0]) >= sliver_width_pt]
    return kept, len(bands) - len(kept)


def _covered_bands(
    rect: Sequence[float], bands: Sequence[tuple[float, float]], ratio: float
) -> list[int]:
    """Band indices this cell covers.  More than one means a real colspan."""
    x0, x1 = rect[0], rect[2]
    out = []
    for i, (a, b) in enumerate(bands):
        overlap = min(x1, b) - max(x0, a)
        if overlap > 0 and overlap >= ratio * (b - a):
            out.append(i)
    return out


def _row_span(rect: Sequence[float], row_tops: Sequence[float]) -> int:
    """How many logical row bands the cell's height covers (a rowspan)."""
    span = 0
    for y0, y1 in row_tops:
        if y1 > rect[1] + 0.5 and y0 < rect[3] - 0.5:
            span += 1
    return max(1, span)


def _is_bold(page: pymupdf.Page, rect: Sequence[float], cache: dict) -> bool:
    key = (round(rect[0], 1), round(rect[1], 1), round(rect[2], 1), round(rect[3], 1))
    if key not in cache:
        bold = False
        try:
            data = page.get_text("dict", clip=pymupdf.Rect(key))
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip() and span["flags"] & 16:
                            bold = True
        except Exception:  # a malformed clip must not sink the whole table
            bold = False
        cache[key] = bold
    return cache[key]


# --------------------------------------------------------------------------- #
# reconstruction
# --------------------------------------------------------------------------- #


def _place(
    page: pymupdf.Page,
    table: Any,
    page_number: int,
    sliver_width_pt: float = SLIVER_WIDTH_PT,
    inner_bboxes: Sequence[Sequence[float]] = (),
) -> ExtractedTable:
    rows_raw = list(table.rows)
    texts = table.extract()
    bands, dropped_slivers = _drop_slivers(_reference_bands(rows_raw), sliver_width_pt)
    if not bands:
        bands = [(table.bbox[0], table.bbox[2])]
    n_cols = len(bands)

    issues: list[str] = []
    if dropped_slivers:
        issues.append(f"dropped {dropped_slivers} sliver column(s) narrower than {sliver_width_pt:g}pt")
    if inner_bboxes:
        issues.append(
            f"{len(inner_bboxes)} nested sub-table(s) removed from this cell's flattened text"
        )

    row_tops = [(row.bbox[1], row.bbox[3]) for row in rows_raw]
    bold_cache: dict = {}
    unplaced = 0
    nested_cells = 0
    logical: list[_LogicalRow] = []
    for row, row_text, (ry0, ry1) in zip(rows_raw, texts, row_tops):
        placed: list[TableCell] = []
        for raw_cell, cell_text in zip(row.cells, row_text):
            if raw_cell is None:
                continue
            text = _clean(cell_text)
            if not text:
                continue                      # empty hairline band: contributes no cell
            if _inside_any(raw_cell, inner_bboxes):
                # The text belongs to a sub-table in this cell; find_tables also
                # reports it against the parent's merged band, which would
                # otherwise concatenate two records into one cell.
                nested_cells += 1
                continue
            covered = _covered_bands(raw_cell, bands, COLUMN_OVERLAP_RATIO)
            if not covered:
                unplaced += 1
                continue
            placed.append(
                TableCell(
                    row=0,
                    col=covered[0],
                    text=text,
                    row_span=_row_span(raw_cell, row_tops),
                    col_span=len(covered),
                    is_header=_is_bold(page, raw_cell, bold_cache),
                )
            )
        if placed:
            logical.append(_LogicalRow(ry0, ry1, placed))
    if unplaced:
        issues.append(f"{unplaced} cell(s) fell outside every surviving column band")
    if nested_cells:
        issues.append(f"{nested_cells} parent cell(s) yielded text to a nested sub-table")
    if not logical:
        raise ValueError("table has no cells that map onto a column band")

    logical = _merge_wrapped_rows(logical)
    for i, row in enumerate(logical):
        for cell in row.cells:
            cell.row = i

    header_count = _header_row_count(logical)
    if header_count == 0:
        issues.append("no header band detected; every row kept as data")
    header_rows = logical[:header_count]
    data_rows = logical[header_count:]
    for row in header_rows:
        for cell in row.cells:
            cell.is_header = True

    columns = _flatten_header(header_rows, n_cols, bool(header_count))
    has_merged_header = any(
        c.col_span > 1 or c.row_span > 1 for row in header_rows for c in row.cells
    )
    if has_merged_header:
        issues.append("header band contains merged (spanning) cells")

    grid_cells = [[c for c in row.cells if c.col < n_cols] for row in data_rows]
    rows = [[_grid_text(row.cells, c) for c in range(n_cols)] for row in data_rows]

    occupied = sum(1 for row in logical for c in row.cells if c.text)
    confidence = round(occupied / (len(logical) * n_cols), 3)

    method = EXTRACTOR_NAME
    if dropped_slivers:
        method += "+sliver-pruned"
    if has_merged_header:
        method += "+colspan-aware"
    if header_count:
        method += "+header-flattened"

    return ExtractedTable(
        columns=columns,
        rows=rows,
        cells=grid_cells,
        header_cells=[r.cells for r in header_rows],
        bbox=tuple(table.bbox),
        page_numbers=[page_number],
        column_bands=bands,
        row_bands=[(r.y0, r.y1) for r in logical],
        header_rows=header_count,
        raw_row_count=len(rows_raw),
        raw_col_count=table.col_count,
        has_merged_header=has_merged_header,
        method=method,
        confidence=confidence,
        issues=issues,
    )


def _grid_text(cells: Sequence[TableCell], col: int) -> str:
    for cell in cells:
        if cell.col == col:
            return cell.as_text()
    return ""


def _merge_wrapped_rows(rows: list[_LogicalRow]) -> list[_LogicalRow]:
    """Fold a row into the one above when it is a wrapped continuation.

    The hairline grid cuts a tall cell into one band per text line, so a single
    logical row such as ``KE0000041`` arrives as four.  A continuation is a row
    that never touches the first column and only repeats columns the row above
    already has -- which is what separates "...the server path opens / when
    browsed directly." from the next record ``KE0000047``.
    """
    merged: list[_LogicalRow] = []
    for row in rows:
        if not merged:
            merged.append(_LogicalRow(row.y0, row.y1, list(row.cells)))
            continue
        previous = merged[-1]
        prev_cols = {c.col for c in previous.cells}
        cur_cols = {c.col for c in row.cells}
        is_continuation = 0 not in cur_cols and bool(cur_cols) and cur_cols <= prev_cols
        if not is_continuation:
            merged.append(_LogicalRow(row.y0, row.y1, list(row.cells)))
            continue
        by_col = {c.col: c for c in previous.cells}
        for cell in row.cells:
            target = by_col.get(cell.col)
            if target is None:
                previous.cells.append(cell)
            else:
                target.text = f"{target.text} {cell.text}".strip()
                target.col_span = max(target.col_span, cell.col_span)
                target.row_span = max(target.row_span, cell.row_span)
        previous.y1 = row.y1
    return merged


def _header_row_count(rows: list[_LogicalRow]) -> int:
    """The maximal leading run of bold rows."""
    count = 0
    for row in rows:
        non_empty = [c for c in row.cells if c.text]
        if not non_empty:
            break
        bold = sum(1 for c in non_empty if c.is_header)
        if bold / len(non_empty) < BOLD_HEADER_RATIO:
            break
        count += 1
    return count


def _flatten_header(header_rows: list[_LogicalRow], n_cols: int, has_header: bool) -> list[str]:
    """One column name per logical column, merging the header band row by row.

    A header cell spanning columns contributes to every column it covers, so the
    two-row header DUBAI / (HOURS (GST), ANALYSTS) flattens to
    "DUBAI HOURS (GST)" and "DUBAI ANALYSTS".
    """
    if not has_header:
        return [f"col_{i + 1}" for i in range(n_cols)]
    names: list[list[str]] = [[] for _ in range(n_cols)]
    for row in header_rows:
        for cell in row.cells:
            for col in range(cell.col, min(cell.col + cell.col_span, n_cols)):
                if cell.text and (not names[col] or names[col][-1] != cell.text):
                    names[col].append(cell.text)
    return _dedupe_columns(
        [" ".join(parts) if parts else f"col_{i + 1}" for i, parts in enumerate(names)]
    )


def _dedupe_columns(columns: Sequence[str]) -> list[str]:
    """Make every column name unique so ``as_records()`` cannot lose a value.

    A header cell that spans several columns gives all of them the same name, and
    a ``{name: value}`` dict then keeps only the last of them.  Numbering the
    repeats keeps the record lossless, and the numbering says plainly that the
    name came from a spanning cell rather than a real column of its own.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            out.append(f"{name} ({seen[name]})")
        else:
            seen[name] = 0
            out.append(name)
    return out


# --------------------------------------------------------------------------- #
# nested tables
# --------------------------------------------------------------------------- #


def _inside_any(rect: Sequence[float], boxes: Sequence[Sequence[float]], ratio: float = 0.5) -> bool:
    return any(containment(rect, box) >= ratio for box in boxes)


def _signature(table: ExtractedTable) -> tuple:
    return (tuple(table.columns), tuple(tuple(r) for r in table.rows))


def _cell_at(table: ExtractedTable, bbox: Sequence[float]) -> TableCell | None:
    """The cell whose logical band contains the centre of ``bbox``."""
    row_index, col_index = _grid_position(table, bbox)
    if row_index is None or col_index is None:
        return None
    for row in table.cells + table.header_cells:
        for cell in row:
            if cell.covers(row_index, col_index):
                return cell
    return None


def _grid_position(table: ExtractedTable, bbox: Sequence[float]) -> tuple[int | None, int | None]:
    """Map a rectangle onto the container's logical (row, column) grid."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    row_index = None
    for i, (y0, y1) in enumerate(table.row_bands):
        if y0 - 0.5 <= cy <= y1 + 0.5:
            row_index = i
            break
    if row_index is None:
        row_index = min(range(len(table.row_bands)), key=lambda i: abs(cy - sum(table.row_bands[i]) / 2), default=0)
    col_index = None
    for i, (x0, x1) in enumerate(table.column_bands):
        if x0 - 0.5 <= cx <= x1 + 0.5:
            col_index = i
            break
    if col_index is None:
        col_index = min(range(len(table.column_bands)),
                        key=lambda i: abs(cx - sum(table.column_bands[i]) / 2), default=0)
    return row_index, col_index


def _attach_nested(tables: list[ExtractedTable], min_containment: float = 0.8) -> None:
    """Attach each inner table to the cell of its container that holds it.

    ``find_tables`` also reports the same sub-grid twice (once with the hairline
    read as extra columns, once without).  After reconstruction the two agree,
    so the duplicate is dropped rather than indexed.

    ``min_containment`` is how much of the inner table has to sit inside the
    outer one before it counts as nested.  It is not named ``containment``
    because that is the geometry helper this now imports.
    """
    for inner in tables:
        container = next(
            (o for o in tables if o is not inner and containment(inner.bbox, o.bbox) >= min_containment),
            None,
        )
        if container is None:
            continue
        target = _cell_at(container, inner.bbox)
        if target is None:
            # A cell that held nothing but the sub-table has no cell object of
            # its own, so an empty one is created to carry the sub-table.
            row_index, col_index = _grid_position(container, inner.bbox)
            if row_index is None or col_index is None:
                continue
            data_index = row_index - container.header_rows
            if not 0 <= data_index < len(container.cells) or not 0 <= col_index < container.col_count:
                continue
            target = TableCell(row=row_index, col=col_index, text="", row_span=1, col_span=1)
            container.cells[data_index].append(target)
        if any(_signature(n) == _signature(inner) for n in target.nested):
            continue
        target.nested.append(inner)
        inner.issues.append(
            f"nested in the page {container.page_number} cell at row {target.row}, col {target.col}"
        )
    for table in tables:
        table.has_nested = bool(table.nested_tables())
        if table.has_nested:
            # The parent cell's own text was withheld from the nested sub-table
            # above, so the rendered grid is re-derived now that the child is
            # known and can be rendered inline.
            table.rows = [
                [_grid_text(row, col) for col in range(table.col_count)] for row in table.cells
            ]


# --------------------------------------------------------------------------- #
# page-crossing tables
# --------------------------------------------------------------------------- #


def _same_grid(a: ExtractedTable, b: ExtractedTable, tolerance: float) -> bool:
    if a.col_count != b.col_count or not a.column_bands or not b.column_bands:
        return False
    aw = max(a.bbox[2] - a.bbox[0], 1e-6)
    bw = max(b.bbox[2] - b.bbox[0], 1e-6)
    for (ax0, ax1), (bx0, bx1) in zip(a.column_bands, b.column_bands):
        ac = ((ax0 + ax1) / 2 - a.bbox[0]) / aw
        bc = ((bx0 + bx1) / 2 - b.bbox[0]) / bw
        if abs(ac - bc) > tolerance:
            return False
    return True


def _consume_header(continuation: ExtractedTable) -> None:
    """A split table repeats its header row at the top of the continuation page."""
    repeated = (
        continuation.header_rows > 0
        or (continuation.rows and list(continuation.columns) and continuation.rows[0] == list(continuation.columns))
    )
    if not repeated:
        return
    if continuation.header_rows:
        continuation.issues.append(
            f"dropped {continuation.header_rows} repeated header row(s) carried over "
            f"from page {continuation.page_number}"
        )
    else:
        continuation.rows = continuation.rows[1:]
        continuation.issues.append(
            f"dropped 1 repeated header row carried over from page {continuation.page_number}"
        )
    continuation.header_rows = 0
    continuation.header_cells = []


def _occupancy(table: ExtractedTable) -> float:
    total = (len(table.header_cells) + table.row_count) * table.col_count
    if not total:
        return 0.0
    filled = sum(1 for row in table.cells + table.header_cells for c in row if c.text)
    return round(filled / total, 3)


def merge_page_continuations(
    by_page: dict[int, list[ExtractedTable]],
    page_heights: dict[int, float] | None = None,
    tolerance: float = COLUMN_BAND_TOLERANCE,
) -> list[ExtractedTable]:
    """Stitch a table cut off at the foot of a page onto the next page's top.

    Returns one entry per logical table, in page then vertical order.  A
    fragment absorbed into its predecessor is dropped from the result; the merged
    table keeps both page numbers and ``spans_page_boundary``.
    """
    absorbed: set[int] = set()
    for page in sorted(by_page):
        height = (page_heights or {}).get(page)
        if not height:
            continue
        for tail in by_page[page]:
            if tail.bbox[3] < height * BOTTOM_MARGIN_FRACTION:
                continue
            for head in by_page.get(page + 1, []):
                if id(head) in absorbed:
                    continue
                if head.bbox[1] > height * TOP_MARGIN_FRACTION:
                    continue
                if not _same_grid(tail, head, tolerance):
                    continue
                _consume_header(head)
                offset = tail.row_count
                for cell_row in head.cells:
                    for cell in cell_row:
                        cell.row += offset
                tail.rows.extend(head.rows)
                tail.cells.extend(head.cells)
                tail.header_cells = tail.header_cells or head.header_cells
                tail.row_bands.extend(head.row_bands)
                tail.bbox = (tail.bbox[0], tail.bbox[1], head.bbox[2], head.bbox[3])
                tail.page_numbers = tail.page_numbers + head.page_numbers
                tail.spans_page_boundary = True
                tail.continued_from = tail.page_numbers[0]
                tail.issues.append(
                    f"continues onto page {head.page_number}: {len(head.rows)} row(s) merged, "
                    f"repeated header dropped"
                )
                tail.method += "+page-crossing-merged"
                tail.confidence = _occupancy(tail)
                absorbed.add(id(head))
                break
    return [t for page in sorted(by_page) for t in by_page[page] if id(t) not in absorbed]


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def extract_page_tables(
    page: pymupdf.Page,
    page_number: int | None = None,
    strategy: str = "lines",
    sliver_width_pt: float = SLIVER_WIDTH_PT,
) -> list[ExtractedTable]:
    """Every table on one page, as structured grids, nested tables attached.

    A bordered box holding one block of prose (a callout, a pull quote, a margin
    note) is detected as a one-cell table and dropped: it carries no row/column
    relationship, and indexing it as a table would invent structure the document
    does not have.
    """
    number = page.number + 1 if page_number is None else page_number
    raw_tables = list(page.find_tables(strategy=strategy).tables)
    tables: list[ExtractedTable] = []
    for raw in raw_tables:
        inner_bboxes = [
            other.bbox
            for other in raw_tables
            if other is not raw and containment(other.bbox, raw.bbox) >= 0.8
        ]
        try:
            table = _place(page, raw, number, sliver_width_pt, inner_bboxes)
        except Exception as exc:  # one bad table must not lose the page
            logger.warning("table extraction failed on page %s: %s", number, exc)
            continue
        if table.col_count < 2 and table.row_count < 2:
            logger.debug("page %s: dropped a %d-cell prose box, not a table", number, table.row_count)
            continue
        if table.row_count == 0 and not table.nested_tables():
            # Every cell was absorbed into the header band, which is what a
            # bordered callout or pull quote looks like to find_tables: a box
            # around one or two blocks of prose.  There is no row to record.
            logger.debug("page %s: dropped a %d-column prose box, no data rows", number, table.col_count)
            continue
        tables.append(table)
    _attach_nested(tables)
    return sorted(tables, key=lambda t: (t.bbox[1], t.bbox[0]))


def extract_tables(
    pdf_path: str | None = None,
    page_numbers: Iterable[int] | None = None,
    strategy: str = "lines",
    sliver_width_pt: float = SLIVER_WIDTH_PT,
    merge_across_pages: bool = True,
) -> TableExtractionResult:
    """Extract the tables from ``page_numbers`` (1-based; default: the whole document).

    Cross-page merging needs a contiguous range: a range that skips the middle
    page of a split table cannot be stitched, and the halves come back as two
    separate tables.
    """
    from ...config import RETRIEVAL

    path = pdf_path or RETRIEVAL.manual_pdf_path
    with pymupdf.open(path) as doc:
        wanted = (
            sorted(set(page_numbers)) if page_numbers is not None
            else list(range(1, doc.page_count + 1))
        )
        by_page: dict[int, list[ExtractedTable]] = {}
        heights: dict[int, float] = {}
        for n in wanted:
            if not 1 <= n <= doc.page_count:
                continue
            page = doc[n - 1]
            heights[n] = page.rect.height
            by_page[n] = extract_page_tables(page, n, strategy, sliver_width_pt)

    tables = (
        merge_page_continuations(by_page, heights)
        if merge_across_pages
        else [t for page in sorted(by_page) for t in by_page[page]]
    )

    provenance = {
        "extractor": EXTRACTOR_NAME,
        "pymupdf_version": getattr(pymupdf, "VersionBind", "unknown"),
        "source_file": path.replace("\\", "/").rsplit("/", 1)[-1],
        "page_numbers": wanted,
        "strategy": strategy,
        "sliver_width_pt": sliver_width_pt,
        "table_count": len(tables),
        "nested_table_count": sum(len(t.nested_tables()) for t in tables),
        "page_crossing_tables": [t.page_numbers for t in tables if t.spans_page_boundary],
        "is_stressor": True,
    }
    return TableExtractionResult(tables=tables, page_numbers=wanted, provenance=provenance)


# The stressor sections of the manual, and the classes each one exercises.
# Written against the pages themselves, not against the table of contents: 8.3
# reads like one long register and is in fact two, a problem register on p29 and
# a known-error register on p30, and the page that breaks is 7.1's journal,
# which runs off the foot of p25 and resumes under a repeated header on p26.
#
# Only extraction outcomes belong here.  Why a picture is hard to read is a
# different question, and it is answered in IMAGE_TRAITS: "dark_theme" is not
# something an extractor emits, and a capability list containing a word no code
# can produce is a test row that can never pass.
STRESSOR_SECTIONS: dict[str, list[str]] = {
    "2.1": ["table", "merged_header"],
    "3.3": ["table", "merged_header"],
    "5.2": ["table", "nested_table"],
    "7.1": ["layout", "form_parsing", "key_value", "table", "page_crossing", "image"],
    "8.3": ["table", "merged_header"],
    "Appendix C": ["table", "merged_header"],
    "10.4": ["ocr", "form_parsing", "checkbox", "image"],
    "11.7": ["table", "nested_table"],
    "11.8": ["ocr", "image"],
    "11.9": ["ocr", "image"],
    "12.2": ["ocr", "image"],
}

# What makes each page's picture hard to read, for whoever wires OCR in.  These
# are properties of the pixels, not outputs of an extractor, and none of them is
# currently handled: the regions are recorded with their bbox and pixel size and
# left unread.
IMAGE_TRAITS: dict[str, list[str]] = {
    "7.1": ["raster_form"],
    "10.4": ["raster_form", "drawn_checkboxes"],
    "11.8": ["dark_theme", "monospace_log"],
    "11.9": ["rotated_image"],
    "12.2": ["arabic_rtl"],
}


def page_capability(section_id: str) -> list[str]:
    """Which stressor classes a manual section exercises.

    A thin view over ``STRESSOR_SECTIONS``, for callers and tests that want the
    declared answer.  Note that ingestion does *not* route on this: routing
    decides from what the page holds, and the declared list is only ever
    compared against that decision, never used to make it.
    """
    return list(STRESSOR_SECTIONS.get(section_id, []))

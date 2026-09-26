"""
S2.6 : route each manual page to the extractors that can read it, and index what
they find as stressor points in the shared KB collection.

The parser in ``manual_parser.py`` reads a page as a column of lines.  That is
right for prose and wrong for everything the manual was designed to stress: a
table read line by line loses the row it belongs to, a form read line by line
loses the label above the value, and a screenshot has no lines at all.  So the
stressor pages go through ``extractors/`` as well, and their output is indexed
with the section it came from, so a retrieval hit can name the section that
produced it.

These points go into ``QDRANT.collection_name`` alongside the KB articles, not
into a collection of their own.  A live query sees one corpus, and a manual
section and a KB article compete for the same five slots; keeping the two apart
would let a no-regression check pass for reasons that have nothing to do with the
extractors.  The two halves are told apart by the ``is_stressor`` payload flag,
which is also how the ablation arms isolate them.

This module is the routing and extraction logic.  The entry point is
``ingest.py::ingest_stressors``; call that, not this.

Run: python -m src.retrieval.ingest_stressors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pymupdf
from qdrant_client import QdrantClient, models

from ..config import QDRANT, RETRIEVAL
from .embedding import embed_dense, embed_sparse
from .extractors.layout import extract_layout_document
from .extractors.tables import STRESSOR_SECTIONS, extract_tables
from .ingest import _ensure_collection, deterministic_point_id
from .manual_parser import parse_manual

logger = logging.getLogger(__name__)

# These points share the KB collection. Kept as a name so a caller reading this
# module does not have to know it is the same one; the value is the single source
# of truth and there is no second collection to drift away from it.
STRESSOR_COLLECTION = QDRANT.collection_name

# Which extractors a page can use.  "text" is the parser's own line reading and
# is always safe; the rest are paid for only when the page can use them.
TEXT = "text"
TABLES = "tables"
LAYOUT = "layout"
OCR = "ocr"

# Where a table has to reach, on the page that starts it and the page that ends
# it, to count as split across the page break.  The manual's text block runs to
# about 94% of the page height, so a table crossing the break lands inside the
# bottom eighth of one page and the top eighth of the next.
BOTTOM_MARGIN_FRACTION = 0.92
TOP_MARGIN_FRACTION = 0.12

# How far the two halves of a split table may differ in column count.  See
# ``_continues_onto``.
COLUMN_TOLERANCE = 1


@dataclass
class PageRoute:
    """What will be run on one page, and why."""

    page_number: int
    section_id: str
    extractors: list[str] = field(default_factory=lambda: [TEXT])
    reasons: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"page": self.page_number, "section_id": self.section_id,
                "extractors": self.extractors, "reasons": self.reasons}


def route_page(page: pymupdf.Page, page_number: int, section_id: str) -> PageRoute:
    """Decide which extractors to spend on a page, from the page itself.

    The decision is made per page rather than per declared capability, because a
    section that declares a table may well have its table on the next page, and
    paying for ``find_tables`` on a page of prose costs seconds and returns
    nothing.  What the page actually holds decides; ``STRESSOR_SECTIONS`` only
    says which classes the section is being measured against.
    """
    from .extractors.layout import page_needs_ocr

    route = PageRoute(page_number=page_number, section_id=section_id)
    if page.find_tables(strategy="lines").tables:
        route.extractors.append(TABLES)
        route.reasons[TABLES] = f"{len(page.find_tables(strategy='lines').tables)} ruled grid(s)"
    if page.get_text("dict").get("blocks"):
        route.extractors.append(LAYOUT)
        route.reasons[LAYOUT] = "positioned text blocks to order"
    if page_needs_ocr(page):
        route.extractors.append(OCR)
        route.reasons[OCR] = "no text layer"
    elif page.get_images(full=True):
        route.extractors.append(OCR)
        route.reasons[OCR] = f"{len(page.get_images(full=True))} image(s) with no text under them"
    return route


def _continues_onto(next_page: pymupdf.Page, this_page: pymupdf.Page) -> bool:
    """Whether ``next_page`` opens by finishing a table left open on ``this_page``.

    7.1's journal and 9.3's journal both run off the foot of a page and resume at
    the top of the next under a repeated header.  A section's own ``page_start``
    does not reach that page, so the routing would label the rest of the table
    under the following section -- the continuation has to be claimed by the
    table that starts on this one.

    The test is the shape of a split plus a loose column check, not an exact
    match.  Two pages each holding a complete table in the middle of the page do
    not match, and neither does a table that ends a third of the way up the
    page.  The column counts are allowed to differ by one because a split table
    is often re-detected one column narrower or wider: 7.1's journal has a
    spanned "Time to close" pair on the first page that comes back as two plain
    columns on the second.  The tolerance is one rather than zero because p27
    holds a 3-column table at the foot and p28 opens with an unrelated 8-column
    one, and a zero tolerance is what tells those two apart.
    """
    tail = this_page.find_tables(strategy="lines").tables
    if not tail or tail[-1].bbox[3] < this_page.rect.height * BOTTOM_MARGIN_FRACTION:
        return False
    head = next_page.find_tables(strategy="lines").tables
    if not head or head[0].bbox[1] > next_page.rect.height * TOP_MARGIN_FRACTION:
        return False
    return abs(head[0].col_count - tail[-1].col_count) <= COLUMN_TOLERANCE


def route_stressor_pages(sections: list | None = None) -> list[PageRoute]:
    """One route per page of every stressor section, in page order."""
    sections = sections if sections is not None else parse_manual()
    by_page: dict[int, str] = {}
    for section in sections:
        if section.section_id in STRESSOR_SECTIONS:
            by_page.setdefault(section.page_start, section.section_id)
    routes: list[PageRoute] = []
    with pymupdf.open(RETRIEVAL.manual_pdf_path) as doc:
        last = doc.page_count
        for page_number in sorted(by_page):
            if not 1 <= page_number <= last:
                continue
            section_id = by_page[page_number]
            routes.append(route_page(doc[page_number - 1], page_number, section_id))
            if page_number < last and _continues_onto(doc[page_number], doc[page_number - 1]):
                routes.append(route_page(doc[page_number], page_number + 1, section_id))
    return routes


def _section_by_id(sections: list) -> dict[str, Any]:
    return {s.section_id: s for s in sections}


def build_stressor_points(sections: list | None = None) -> tuple[list[models.PointStruct], dict]:
    """Every stressor reading as a point, tagged with the section it came from.

    Three kinds of point, because the three stressors fail differently: a table
    read as a grid, a page read in column order, and a form read as pairs.  A
    table and the page around it are both indexed -- the grid answers "what are
    the hours" and the page answers "what does the section say", and a question
    about either should find this section.

    ``capability_class`` names the artifact, not the section's declared test:
    the same page can be read two ways and both are worth a hit, so a form page
    gets a ``key_value`` point and a ``layout`` point.  What a section is
    measured against is ``STRESSOR_SECTIONS``, and the two are compared in the
    coverage matrix rather than conflated here.
    """
    sections = sections if sections is not None else parse_manual()
    by_id = _section_by_id(sections)
    routes = route_stressor_pages(sections)
    pages = [r.page_number for r in routes]

    points: list[models.PointStruct] = []
    added = {"table": 0, "layout": 0, "form": 0, "ocr_region": 0}
    for route in routes:
        logger.info("p%-3d %-12s %s", route.page_number, route.section_id,
                    ", ".join(e for e in route.extractors if e != TEXT) or "text only")

    tables = extract_tables(page_numbers=pages)
    for table in tables.tables:
        section = by_id.get(_section_for_page(routes, table.page_numbers[0]))
        if section is None or not table.columns:
            continue
        capability = "table"
        record = "\n".join(
            [f"{section.section_label} {section.title}",
             f"Table on page {'-'.join(str(p) for p in table.page_numbers)}:",
             table.to_markdown()]
        )
        points.append(models.PointStruct(
            id=deterministic_point_id(f"stressor:{section.section_id}:table:{table.bbox}", section.version, 0),
            vector={"dense": embed_dense(record), "sparse": models.SparseVector(**embed_sparse(record))},
            payload=_payload(section, record, "stressor", capability, "tables", table.page_numbers,
                             {"has_merged_header": table.has_merged_header,
                              "has_nested": table.has_nested,
                              "nested_count": len(table.nested_tables()),
                              "spans_page_boundary": table.spans_page_boundary,
                              "columns": table.columns[:12],
                              "row_count": table.row_count,
                              "method": table.method,
                              "issues": table.issues}),
        ))
        added["table"] += 1

    for result in _layout_results(pages):
        section = by_id.get(_section_for_page(routes, result.page_number))
        if section is None:
            continue
        capability = "layout"
        record = f"{section.section_label} {section.title}\n{result.text}"
        points.append(models.PointStruct(
            id=deterministic_point_id(f"stressor:{section.section_id}:layout:{result.page_number}", section.version, 0),
            vector={"dense": embed_dense(record), "sparse": models.SparseVector(**embed_sparse(record))},
            payload=_payload(section, record, "stressor", capability, "layout", [result.page_number],
                             {"column_count": result.column_count,
                              "reading_order_changed": result.provenance["reading_order_changed"],
                              "has_text_layer": result.has_text_layer,
                              "needs_ocr": result.needs_ocr,
                              "ocr_region_count": len(result.ocr_regions)}),
        ))
        added["layout"] += 1

        if result.form_fields:
            pairs = "; ".join(f"{f.key}: {f.value}" for f in result.form_fields)
            form_record = f"{section.section_label} {section.title}\nForm fields: {pairs}"
            points.append(models.PointStruct(
                id=deterministic_point_id(
                    f"stressor:{section.section_id}:form:{result.page_number}", section.version, 0),
                vector={"dense": embed_dense(form_record),
                        "sparse": models.SparseVector(**embed_sparse(form_record))},
                payload=_payload(section, form_record, "stressor", "key_value", "layout", [result.page_number],
                                 {"field_count": len(result.form_fields),
                                  "fields": [f.as_dict() for f in result.form_fields],
                                  "checkbox_count": len(result.checkboxes)}),
            ))
            added["form"] += 1

        for region in result.ocr_regions:
            # The region's own words are not in the text layer, so what can be
            # indexed is the fact of it and where it is.  Saying so is better
            # than a page summary that reads as though the screenshot were read.
            note = (f"{section.section_label} {section.title}\n"
                    f"Screenshot on page {result.page_number} with no text layer of its own; "
                    f"it needs OCR and is not indexed as text. Region in points: "
                    f"{tuple(round(v) for v in region['bbox'])}.")
            points.append(models.PointStruct(
                id=deterministic_point_id(
                    f"stressor:{section.section_id}:ocr:{result.page_number}:{region['bbox'][0]:.0f}",
                    section.version, 0),
                vector={"dense": embed_dense(note), "sparse": models.SparseVector(**embed_sparse(note))},
                payload=_payload(section, note, "stressor", "ocr", "ocr", [result.page_number],
                                 {"region_bbox": [round(v, 1) for v in region["bbox"]],
                                  "region_px": region["px"],
                                  "ocr_applied": False,
                                  "reason": "no ocr.py on this branch; region recorded, not read"}),
            ))
            added["ocr_region"] += 1

    stats = {"points": len(points), "pages": len(pages), "by_kind": added,
             "tables": tables.provenance.get("table_count", 0),
             "nested_tables": tables.provenance.get("nested_table_count", 0),
             "page_crossing": tables.provenance.get("page_crossing_tables", [])}
    return points, stats


def _layout_results(pages: list[int]) -> list:
    with pymupdf.open(RETRIEVAL.manual_pdf_path) as doc:
        return extract_layout_document(doc, pages)


def _section_for_page(routes: list[PageRoute], page_number: int) -> str:
    for route in routes:
        if route.page_number == page_number:
            return route.section_id
    return ""


def _payload(section, text: str, source: str, capability: str, extractor: str,
             page_numbers: list[int], extra: dict) -> dict:
    """A stressor point, carrying the manual's own keys plus what it was for.

    The S1.4 payload keys are kept whole so a stressor point is filterable by the
    same fields as a KB article and passes the same retrieval filters.  The
    stressor keys are added alongside, not in place of anything.

    ``content_hash`` is deliberately absent.  ``sync_kb`` treats any point in the
    collection carrying an ``article_id`` and no ``content_hash`` as an article
    that has disappeared from ServiceNow, and deletes it; a stressor point would
    be wiped by the next sync.  ``sync_kb`` is taught to skip stressor points
    instead, and the missing hash is a second line of defence.
    """
    return {
        "number": section.kb_number, "article_id": section.section_label, "title": section.title,
        "section": section.section_id, "section_id": section.section_id,
        "section_label": section.section_label, "category": section.category,
        "service": section.service, "workflow_state": section.workflow_state,
        "version": section.version, "security_level": section.security_level,
        "chunk_index": 0, "page_start": section.page_start,
        "text": text, "source": source,
        "is_stressor": True, "capability_class": capability, "extractor": extractor,
        "extractor_pages": list(page_numbers), "provenance": extra,
    }


def upsert_stressor_points(client: QdrantClient, points: list[models.PointStruct]) -> dict:
    """Write the stressor points into the shared collection.

    The collection is created by ``ingest.py``'s own ``_ensure_collection`` so
    that a stressor run cannot bring up a collection with the wrong vectors, the
    wrong distance, or a different embedding model from the one the KB articles
    were indexed with.
    """
    _ensure_collection(client)
    collection = QDRANT.collection_name
    client.upsert(collection, points)
    info = client.get_collection(collection)
    return {
        "collection": collection,
        "points_written": len(points),
        "collection_total": info.points_count,
        "shared_with_kb": True,
    }


def drop_stressor_points(client: QdrantClient) -> int:
    """Remove only the stressor points, leaving the KB articles alone.

    This is the rollback for a bad extraction run, and it is also how the
    no-regression check is set up: score the 37 baseline queries with the manual
    mixed in, drop it, score them again, and compare.  ``sync_kb`` is not a
    substitute -- it is the thing that would delete them by accident.
    """
    _ensure_collection(client)
    client.delete(
        QDRANT.collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[models.FieldCondition(
                key="is_stressor", match=models.MatchValue(value=True))])
        ),
    )
    return count_stressor_points(client)


def count_stressor_points(client: QdrantClient) -> int:
    return client.count(
        QDRANT.collection_name,
        count_filter=models.Filter(must=[models.FieldCondition(
            key="is_stressor", match=models.MatchValue(value=True))]),
        exact=True,
    ).count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import argparse

    from .ingest import ingest_stressors

    ap = argparse.ArgumentParser(description="index the manual's extracted pages")
    ap.add_argument("--drop", action="store_true",
                    help="remove the stressor points and leave the KB articles alone")
    args = ap.parse_args()
    if args.drop:
        print("dropped", drop_stressor_points(_client()), "stressor point(s)")
    else:
        ingest_stressors()


def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT.url, check_compatibility=False)

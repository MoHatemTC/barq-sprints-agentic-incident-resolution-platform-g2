"""
Extractors for the S2.6 RAG corpus hardening scope: the document stressors that
break a naive ``page.get_text()`` pipeline.

  tables.py  -- merged/colspan headers, nested tables, tables split across a page
  layout.py  -- reading order for multi-column pages, form key/value + checkbox
                structure, and the "does this page have a text layer?" gate
  ocr.py     -- lives on Marcelino's branch (S3.3); imported lazily, never
                re-implemented here. See ``ingest.route_page``.

The provenance dictionary every extractor returns mirrors
``OCRExtractionResult.provenance``: extractor name, source, page numbers,
method and ``is_stressor``.
"""

from .layout import LayoutExtractionResult, extract_layout, page_needs_ocr
from .tables import ExtractedTable, TableCell, TableExtractionResult, extract_tables

__all__ = [
    "ExtractedTable",
    "LayoutExtractionResult",
    "TableCell",
    "TableExtractionResult",
    "extract_layout",
    "extract_tables",
    "page_needs_ocr",
]

"""
Extractors for the S2.6 RAG corpus hardening scope: the document stressors that
break a naive ``page.get_text()`` pipeline.

  tables.py  -- merged/colspan headers, nested tables, tables split across a page
  layout.py  -- reading order for multi-column pages, form key/value + checkbox
                structure, and the "does this page have a text layer?" gate
  ocr.py     -- Marcelino's S3.3 OCR extractor (pytesseract). Imported lazily and
                never re-implemented here; see ``ingest_stressors.route_page``.

The provenance dictionary every extractor returns mirrors
``OCRExtractionResult.provenance``: extractor name, source, page numbers,
method and ``is_stressor``.
"""

from .layout import LayoutExtractionResult, extract_layout, page_needs_ocr
from .tables import ExtractedTable, TableCell, TableExtractionResult, extract_tables

__all__ = [
    "ExtractedTable",
    "LayoutExtractionResult",
    "OCRExtractionResult",
    "TableCell",
    "TableExtractionResult",
    "extract_layout",
    "extract_ocr",
    "extract_ocr_from_image",
    "extract_tables",
    "ocr_available",
    "page_needs_ocr",
]


def __getattr__(name: str):
    """Reach ocr.py's names on demand, and only when asked for one.

    ocr.py needs pytesseract and the tesseract binary. Neither is a hard
    dependency of S2.6 -- the corpus hardens fine without them, and the router
    still records image regions when OCR is absent. Importing it eagerly would
    make ``import src.retrieval.extractors`` fail on a box with no tesseract,
    which would take the table and layout extractors down with it.
    """
    if name in {"OCRExtractionResult", "extract_ocr", "extract_ocr_from_image", "ocr_available"}:
        from . import ocr
        return getattr(ocr, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

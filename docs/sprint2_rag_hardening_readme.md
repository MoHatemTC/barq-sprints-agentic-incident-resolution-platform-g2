# Sprint 2 RAG Corpus Hardening (S2.6)

## Purpose
The RAG Corpus Hardening effort (Sprint 2.6) aims to ingest complex, non-standard IT documentation (referred to as "stressors") into our Qdrant vector database. This ensures the RAG pipeline can retrieve knowledge from previously inaccessible formats like images and scanned PDFs.

## Supported Extractors

Currently, the ingestion pipeline relies on specialized extractors to parse complex formats.

### OCR Extractor (`src/retrieval/extractors/ocr.py`)
This module handles images (PNG, JPG, TIFF) and PDF files.
- **Dependencies**: Relies on `pytesseract` (pinned to v0.3.13), `Pillow` (v12.3.0), and `pdf2image` (v1.17.0).
- **Functionality**: Extracts text from images and generates an average Confidence Score for the extraction.
- **Preprocessing**: Includes an optional preprocessing pipeline that converts images to grayscale, increases contrast, and applies upscaling using Lanczos resampling. This significantly improves OCR accuracy on fuzzy screenshots.
- **Provenance**: Captures vital metadata (extractor name, Tesseract version, DPI, confidence score) and embeds it within the output `Article` payload.

*Note: Table and Layout extractors are planned for future sprints.*

## Ingestion Integration (`src/retrieval/ingest.py`)
The `load_stressors()` function orchestrates the parsing of these complex documents. It scans specific directories (e.g., `data/corpus/stressors/ocr/`), routes files to the appropriate extractor, and wraps the resulting text and provenance metadata into standard `Article` objects. These are then ingested seamlessly alongside standard ServiceNow articles.

## How to Test
A CLI utility is provided for testing the OCR pipeline locally:
```bash
python scripts/check_ocr.py "path/to/image.png" --preprocess
```

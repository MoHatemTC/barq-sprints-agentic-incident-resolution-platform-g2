# Sprint 2 RAG Corpus Hardening Report

## Executive Summary
This report summarizes the completion of the RAG Corpus Hardening (S2.6) task. The primary objective was to expand our retrieval pipeline to handle non-text "stressor" assets, specifically image-based IT documentation and screenshots, while preserving the strict Qdrant payload schema contract established in Sprint 1.4.

## Implementation Details

### OCR Extractor Integration
We successfully implemented the OCR extractor using `pytesseract`. To address poor extraction accuracy on low-quality screenshots, an image enhancement pipeline (`Pillow`) was added to apply grayscale conversion, contrast boosting, and structural upscaling.

### Provenance Tracking
To ensure traceability and debugging for retrieval failures, strict provenance metadata is captured during extraction. This metadata includes the exact library versions used, the extraction configuration (PSM, DPI), and a calculated confidence score. This provenance is embedded directly into the article body text, bypassing the need to alter the downstream Qdrant payload schema.

### Ingestion Pipeline
The `src/retrieval/ingest.py` script was modified to detect files inside `data/corpus/stressors/ocr/`. It gracefully handles import errors or missing binaries without crashing the primary text ingestion pipeline.

## Evaluation & Next Steps
Initial testing via `scripts/check_ocr.py` demonstrates high fidelity text extraction, particularly when the `--preprocess` flag is active. Evaluation ablation scripts will include these new assets to benchmark retrieval performance under stress conditions.

*(Note: Table and Layout extractors were scoped out of this report phase.)*

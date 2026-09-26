"""Tests for src.retrieval.extractors.ocr."""

import pytest
from unittest.mock import patch, MagicMock
from src.retrieval.extractors.ocr import extract_ocr

@patch("src.retrieval.extractors.ocr.pytesseract")
@patch("src.retrieval.extractors.ocr.Image")
@patch("src.retrieval.extractors.ocr.os.path.exists")
def test_extract_ocr_basic(mock_exists, mock_image, mock_pytesseract):
    mock_exists.return_value = True
    
    # Mock image open
    mock_img_instance = MagicMock()
    mock_image.open.return_value = mock_img_instance
    
    # Mock pytesseract output
    mock_pytesseract.image_to_data.return_value = {
        'text': ['Hello', 'World', ''],
        'conf': ['95.5', '90.0', '-1']
    }
    
    # Mock tesseract version
    mock_version = MagicMock()
    mock_version.base_version = "5.4.0"
    mock_pytesseract.get_tesseract_version.return_value = mock_version
    
    result = extract_ocr("test.png")
    
    assert result.text == "Hello World"
    assert result.confidence_score == 92.75 # (95.5 + 90.0) / 2
    assert result.page_count == 1
    assert result.provenance["extractor"] == "pytesseract"
    assert result.provenance["tesseract_cmd_version"] == "5.4.0"
    assert result.provenance["preprocess_applied"] is False


@patch("src.retrieval.extractors.ocr._preprocess_image")
@patch("src.retrieval.extractors.ocr.pytesseract")
@patch("src.retrieval.extractors.ocr.Image")
@patch("src.retrieval.extractors.ocr.os.path.exists")
def test_extract_ocr_preprocess(mock_exists, mock_image, mock_pytesseract, mock_preprocess):
    mock_exists.return_value = True
    
    mock_img_instance = MagicMock()
    mock_image.open.return_value = mock_img_instance
    
    mock_pytesseract.image_to_data.return_value = {
        'text': ['Preprocessed'],
        'conf': ['99.0']
    }
    
    result = extract_ocr("test.png", preprocess=True)
    
    assert mock_preprocess.called
    assert result.text == "Preprocessed"
    assert result.provenance["preprocess_applied"] is True


@patch("src.retrieval.extractors.ocr.Image")
@patch("src.retrieval.extractors.ocr.pytesseract")
@patch("src.retrieval.extractors.ocr.os.path.exists")
def test_extract_ocr_file_not_found(mock_exists, mock_tess, mock_image):
    mock_exists.return_value = False

    # pytesseract/Image are patched too: extract_ocr() guards on importability
    # before it looks at the path, so without them this test only passes on a
    # machine that happens to have OCR installed. The subject here is the
    # missing-file path, and that should hold everywhere.
    with pytest.raises(FileNotFoundError):
        extract_ocr("missing.png")

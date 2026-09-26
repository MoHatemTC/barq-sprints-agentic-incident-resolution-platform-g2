"""
OCR extraction for image-based documentation (Marcelino, S3.3).

System dependencies, neither of which pip can install for you:

  1. Tesseract OCR engine. pytesseract is only a binding; it shells out to a
     ``tesseract`` binary that must be on PATH.
         Debian/Ubuntu : sudo apt-get install -y tesseract-ocr
         macOS         : brew install tesseract
         Windows       : choco install tesseract
  2. Poppler, **only** for the PDF branch below, because ``pdf2image`` drives
     ``pdftoppm``:
         Debian/Ubuntu : sudo apt-get install -y poppler-utils
         macOS         : brew install poppler

If the caller already holds a rendered image -- which is the case for the S2.6
router, which renders an image region off a PDF page with PyMuPDF's
``page.get_pixmap()`` -- neither Poppler nor pdf2image is involved at all. Use
``extract_ocr_from_image()`` for that path; ``extract_ocr()`` is the
whole-file/standalone entry point and keeps the pdf2image branch for the
``scripts/check_ocr.py`` workflow.

Pinned in requirements.txt: pytesseract==0.3.13 (matching
``EXPECTED_PYTESSERACT_VERSION`` below), pillow, pdf2image==1.17.0.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import os

# Pinned version assertions/checks can go here or in the environment config.
# We expect these specific versions for reproducibility.
# 0.3.13 is the current release on PyPI; requirements.txt pins the same version.
# (0.3.11 and 0.3.12 were never published, so the pin skips straight over them.)
EXPECTED_PYTESSERACT_VERSION = "0.3.13"

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

logger = logging.getLogger(__name__)


@dataclass
class OCRExtractionResult:
    """Result of an OCR extraction including provenance metadata."""
    text: str
    confidence_score: float
    page_count: int
    provenance: Dict[str, Any] = field(default_factory=dict)


def _preprocess_image(img):
    """Enhance image to improve OCR confidence."""
    from PIL import ImageEnhance
    # 1. Convert to grayscale
    img = img.convert('L')
    
    # 2. Increase contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    
    # 3. Scale up the image (x2) to give Tesseract more pixels to work with
    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
    
    return img

def ocr_available() -> tuple[bool, str]:
    """Is OCR actually usable here? Returns (ok, reason-if-not).

    Two separate things have to be true and they fail differently: the
    pytesseract binding has to be importable, and the tesseract *binary* has to
    be on PATH. The binding imports fine on a box with no engine installed, so
    checking the import alone would report ready and then fail at call time.
    """
    if Image is None or pytesseract is None:
        return False, (f"pytesseract=={EXPECTED_PYTESSERACT_VERSION} / Pillow not importable")
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:                      # pytesseract raises if the binary is missing
        return False, f"tesseract binary not found on PATH ({type(e).__name__})"
    return True, ""


def extract_ocr_from_image(img, *, source: str = "<in-memory>", page_count: int = 1,
                           dpi: int = 300, preprocess: bool = False,
                           psm: int = 3) -> OCRExtractionResult:
    """Run OCR over an already-rendered image.

    This is the entry point for callers that do not have a file to hand --
    ``ingest_stressors`` renders an image region off a PDF page with
    ``page.get_pixmap()`` and passes the resulting image straight in, so no
    temporary file, no pdf2image and no Poppler.

    The confidence scoring is the mean of pytesseract's per-word confidences,
    ignoring the -1 it reports for structural blocks that carry no text.
    """
    if Image is None or pytesseract is None:
        raise ImportError(
            f"OCR libraries missing. Please install pytesseract=={EXPECTED_PYTESSERACT_VERSION} and Pillow."
        )

    provenance = {
        "extractor": "pytesseract",
        "pytesseract_version": getattr(pytesseract, "__version__", "unknown"),
        "tesseract_cmd_version": "unknown",  # populated below
        "source_file": source,
        "dpi": dpi,
        "preprocess_applied": preprocess,
        "psm": psm,
        "is_stressor": True,
    }

    try:
        provenance["tesseract_cmd_version"] = pytesseract.get_tesseract_version().base_version
    except Exception as e:
        logger.warning(f"Could not determine Tesseract version: {e}")

    full_text = []
    total_conf = 0.0
    valid_conf_blocks = 0

    custom_config = f"--psm {psm}"

    for i, image in enumerate(img if isinstance(img, (list, tuple)) else [img]):
        if preprocess:
            image = _preprocess_image(image)

        # Extract data (including confidence)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=custom_config)
        page_text = []

        for j, word in enumerate(data.get('text', [])):
            if word.strip():
                page_text.append(word)
                conf = float(data['conf'][j])
                if conf >= 0:  # pytesseract returns -1 for empty blocks
                    total_conf += conf
                    valid_conf_blocks += 1

        full_text.append(" ".join(page_text))

    avg_confidence = (total_conf / valid_conf_blocks) if valid_conf_blocks > 0 else 0.0

    return OCRExtractionResult(
        text="\n\n".join(full_text).strip(),
        confidence_score=round(avg_confidence, 2),
        page_count=page_count,
        provenance=provenance
    )


def extract_ocr(file_path: str, dpi: int = 300, preprocess: bool = False, psm: int = 3) -> OCRExtractionResult:
    """
    Extract text from an image or PDF using OCR.

    Args:
        file_path: Absolute or relative path to the image/PDF file.
        dpi: Target DPI for rendering (improves OCR quality).
        preprocess: If True, applies grayscale, contrast enhancement, and scaling.
        psm: Page Segmentation Mode (default 3 = Fully automatic page segmentation).

    Returns:
        OCRExtractionResult containing the text and provenance metadata.
    """
    if Image is None or pytesseract is None:
        raise ImportError(
            f"OCR libraries missing. Please install pytesseract=={EXPECTED_PYTESSERACT_VERSION} and Pillow."
        )

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Stressor file not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    # Handle PDFs vs Images
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_path
        except ImportError:
            raise ImportError("pdf2image==1.17.0 is required for PDF OCR extraction.")
        images = convert_from_path(file_path, dpi=dpi)
    elif ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
        images = [Image.open(file_path)]
    else:
        raise ValueError(f"Unsupported file extension for OCR: {ext}")

    return extract_ocr_from_image(
        images,
        source=os.path.basename(file_path),
        page_count=len(images),
        dpi=dpi,
        preprocess=preprocess,
        psm=psm,
    )

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import os

# Pinned version assertions/checks can go here or in the environment config.
# We expect these specific versions for reproducibility.
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
    
    # Provenance metadata to satisfy S2.6 requirements
    provenance = {
        "extractor": "pytesseract",
        "pytesseract_version": getattr(pytesseract, "__version__", "unknown"),
        "tesseract_cmd_version": "unknown", # populated below
        "source_file": os.path.basename(file_path),
        "dpi": dpi,
        "preprocess_applied": preprocess,
        "psm": psm,
        "is_stressor": True
    }

    try:
        provenance["tesseract_cmd_version"] = pytesseract.get_tesseract_version().base_version
    except Exception as e:
        logger.warning(f"Could not determine Tesseract version: {e}")

    images = []
    
    # Handle PDFs vs Images
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(file_path, dpi=dpi)
        except ImportError:
            raise ImportError("pdf2image==1.17.0 is required for PDF OCR extraction.")
    elif ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
        images = [Image.open(file_path)]
    else:
        raise ValueError(f"Unsupported file extension for OCR: {ext}")

    full_text = []
    total_conf = 0.0
    valid_conf_blocks = 0
    
    custom_config = f"--psm {psm}"

    for i, img in enumerate(images):
        if preprocess:
            img = _preprocess_image(img)
            
        # Extract data (including confidence)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config=custom_config)
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
        page_count=len(images),
        provenance=provenance
    )

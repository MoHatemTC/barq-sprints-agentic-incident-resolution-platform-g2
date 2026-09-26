import sys
import os
import json

# Ensure the root of the project is in the PYTHONPATH so we can import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.retrieval.extractors.ocr import extract_ocr

# If on Windows, configure the path to the Tesseract executable in case it's not in PATH
if sys.platform == 'win32':
    import pytesseract
    default_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if os.path.exists(default_path):
        pytesseract.pytesseract.tesseract_cmd = default_path

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_ocr.py <path_to_image_or_pdf>")
        print("Example: python check_ocr.py my_test_image.png")
        sys.exit(1)

    file_path = [arg for arg in sys.argv[1:] if not arg.startswith('--')][0]
    use_preprocess = '--preprocess' in sys.argv
    
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist.")
        sys.exit(1)

    print(f"Running OCR on: {file_path} (Preprocessing: {use_preprocess})")
    print("-" * 50)
    
    try:
        # Run the extractor
        result = extract_ocr(file_path, preprocess=use_preprocess)
        
        print("\n=== EXTRACTED TEXT ===")
        print(result.text)
        print("\n=== PROVENANCE METADATA ===")
        print(json.dumps(result.provenance, indent=2))
        print(f"\nConfidence Score: {result.confidence_score}")
        print(f"Page Count: {result.page_count}")
        
    except Exception as e:
        print(f"\nError during OCR extraction: {e}")
        print("\nNote: Please ensure pytesseract and Pillow are installed.")
        print("Run: pip install pytesseract==0.3.13 Pillow")
        sys.exit(1)

if __name__ == "__main__":
    main()

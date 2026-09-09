"""
Splits article body text into chunks. Each numbered procedure/section stays
together in a single chunk -- never split mid-procedure.
"""

import re


def chunk_article(body_text: str) -> list[str]:
    sections = re.split(r"(?=^## )", body_text, flags=re.MULTILINE)
    chunks = [s.strip() for s in sections if s.strip()]
    return chunks if chunks else [body_text.strip()]
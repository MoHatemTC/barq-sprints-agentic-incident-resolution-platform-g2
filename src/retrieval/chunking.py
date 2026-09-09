"""
Splits article body text into (section, chunk_text) pairs. Each numbered
procedure/section stays together in a single chunk -- never split
mid-procedure. Section label is preserved for the payload's `section` field.
"""

import re


def chunk_article(body_text: str) -> list[tuple[str, str]]:
    """
    Returns a list of (section_label, chunk_text) tuples.
    Splits on markdown '## Header' boundaries; each section becomes one chunk.
    If no headers are found, the whole body is a single chunk labeled 'body'.
    """
    parts = re.split(r"(?=^## )", body_text, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return [("body", body_text.strip())]

    chunks = []
    for part in parts:
        header_match = re.match(r"^##\s*(.+)$", part.splitlines()[0]) if part.splitlines() else None
        section_label = header_match.group(1).strip() if header_match else "body"
        chunks.append((section_label, part))

    return chunks
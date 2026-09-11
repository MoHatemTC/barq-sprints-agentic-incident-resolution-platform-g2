"""
Splits article body text into (section, chunk_text) pairs. Each numbered
procedure/section stays together in a single chunk -- never split
mid-procedure. Section label is preserved for the payload's `section` field.

HTML preprocessing runs before section splitting, so ServiceNow-style HTML
KB content is converted to clean Markdown (preserving code blocks and
technical tokens) before chunking ever sees it. Plain Markdown/text input
passes through preprocessing unchanged.
"""

import re

from src.retrieval.preprocessing import strip_article_html


def chunk_article(content: str) -> list[tuple[str, str]]:
    """
    Returns a list of (section_label, chunk_text) tuples.
    Splits on markdown '## Header' boundaries; each section becomes one chunk.
    If no headers are found, the whole body is a single chunk labeled 'body'.
    """
    content = strip_article_html(content)

    parts = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return [("body", content.strip())]

    chunks = []
    for part in parts:
        header_match = re.match(r"^##\s*(.+)$", part.splitlines()[0]) if part.splitlines() else None
        section_label = header_match.group(1).strip() if header_match else "body"
        chunks.append((section_label, part))

    return chunks
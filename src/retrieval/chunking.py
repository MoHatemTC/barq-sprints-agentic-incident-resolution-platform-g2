"""Split preprocessed article content into labelled, bounded chunks."""

import re

from src.config import CHUNKING
from src.retrieval.preprocessing import strip_article_html


def _split_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split oversized text into overlapping windows."""
    if len(text) <= chunk_size:
        return [text]

    step = chunk_size - chunk_overlap
    windows = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        windows.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return windows


def chunk_article(
    content: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> list[tuple[str, str]]:
    """Return `(section, text)` chunks using configured size and overlap."""
    chunk_size = CHUNKING.chunk_size if chunk_size is None else chunk_size
    chunk_overlap = CHUNKING.chunk_overlap if chunk_overlap is None else chunk_overlap

    content = strip_article_html(content)

    parts = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        parts = [content.strip()]
        sections = [("body", parts[0])]
    else:
        sections = []
        for part in parts:
            header_match = re.match(r"^##\s*(.+)$", part.splitlines()[0]) if part.splitlines() else None
            section_label = header_match.group(1).strip() if header_match else "body"
            sections.append((section_label, part))

    chunks = []
    for section_label, section_text in sections:
        for sub_chunk in _split_with_overlap(section_text, chunk_size, chunk_overlap):
            chunks.append((section_label, sub_chunk))

    return chunks

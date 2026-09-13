"""
Splits article body text into (section, chunk_text) pairs. Each numbered
procedure/section stays together in a single chunk -- never split
mid-procedure -- unless a section exceeds the configured chunk size, in
which case it is further split into overlapping windows (CHUNK_SIZE /
CHUNK_OVERLAP, from config) so no single chunk grows unbounded. Section
label is preserved for the payload's `section` field, including on any
sub-chunks produced from an oversized section.

HTML preprocessing runs before section splitting, so ServiceNow-style HTML
KB content is converted to clean Markdown (preserving code blocks and
technical tokens) before chunking ever sees it. Plain Markdown/text input
passes through preprocessing unchanged.
"""

import re

from src.config import CHUNKING
from src.retrieval.preprocessing import strip_article_html


def _split_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """
    Splits text into windows of at most chunk_size characters, with
    chunk_overlap characters of overlap between consecutive windows.
    Returns [text] unchanged if it already fits within chunk_size.
    """
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
    """
    Returns a list of (section_label, chunk_text) tuples.
    Splits on markdown '## Header' boundaries; each section becomes one
    chunk, unless it exceeds chunk_size, in which case it is further split
    into overlapping sub-chunks (still tagged with the same section label).
    If no headers are found, the whole body is treated as a single 'body'
    section subject to the same size/overlap splitting.

    chunk_size / chunk_overlap default to the values in src.config.CHUNKING
    (CHUNK_SIZE / CHUNK_OVERLAP env vars); the parameters exist so tests
    can exercise specific values without relying on process-wide env vars.
    """
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
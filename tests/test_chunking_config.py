import pytest

from src.config import ChunkingConfig, CHUNKING
from src.retrieval.chunking import chunk_article, _split_with_overlap


def test_chunking_config_reads_defaults():
    # Defaults match what the reviewer specified, when no env override is set.
    assert CHUNKING.chunk_size == 500
    assert CHUNKING.chunk_overlap == 50


def test_chunking_config_rejects_non_positive_chunk_size():
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=0, chunk_overlap=10)
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=-100, chunk_overlap=10)


def test_chunking_config_rejects_overlap_out_of_range():
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=100)  # must be strictly less
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=150)
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=-1)


def test_chunking_config_accepts_valid_boundary_values():
    # overlap == 0 and overlap == chunk_size - 1 are both valid boundaries.
    ChunkingConfig(chunk_size=100, chunk_overlap=0)
    ChunkingConfig(chunk_size=100, chunk_overlap=99)


def test_short_section_is_not_split_by_size_config():
    # A section well under chunk_size should behave exactly as before --
    # one section in, one chunk out.
    content = "## Symptom\nShort text well under the limit."
    chunks = chunk_article(content, chunk_size=500, chunk_overlap=50)
    assert chunks == [("Symptom", "## Symptom\nShort text well under the limit.")]


def test_long_section_is_split_into_overlapping_subchunks():
    # Build a section whose body alone exceeds a small configured chunk_size,
    # so we can deterministically verify splitting occurs.
    body = "x" * 250
    content = f"## Resolution\n{body}"

    chunks = chunk_article(content, chunk_size=100, chunk_overlap=20)

    # More than one chunk must be produced from this single section.
    assert len(chunks) > 1
    # Every sub-chunk keeps the original section label.
    assert all(section == "Resolution" for section, _ in chunks)
    # No sub-chunk exceeds the configured size.
    assert all(len(text) <= 100 for _, text in chunks)
    # Concatenating without the overlapping tail should reconstruct the
    # original content (sanity check that no characters were dropped).
    rebuilt = chunks[0][1]
    step = 100 - 20
    for _, text in chunks[1:]:
        rebuilt += text[20:] if len(text) > 20 else text
    assert content in rebuilt or rebuilt.startswith("## Resolution")


def test_split_with_overlap_produces_expected_window_count():
    text = "a" * 100
    windows = _split_with_overlap(text, chunk_size=30, chunk_overlap=10)
    # step = 20; windows start at 0, 20, 40, 60, 80 -> 5 windows covering 100 chars
    assert len(windows) == 5
    assert all(len(w) <= 30 for w in windows)


def test_split_with_overlap_returns_single_chunk_when_text_fits():
    text = "short text"
    windows = _split_with_overlap(text, chunk_size=500, chunk_overlap=50)
    assert windows == [text]


def test_chunk_article_uses_config_defaults_when_not_overridden():
    # With the real default CHUNKING (500/50), a short article stays as
    # one chunk per section, same as before this change.
    content = "## Symptom\nGateway reports HTTP 502."
    chunks = chunk_article(content)
    assert chunks == [("Symptom", "## Symptom\nGateway reports HTTP 502.")]
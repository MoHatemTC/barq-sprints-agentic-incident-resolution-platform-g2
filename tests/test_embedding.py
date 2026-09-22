"""Tests for the LiteLLM-backed dense embedding implementation.

All tests mock the httpx.post call so no real API requests are made.
Sparse embedding tests verify the fastembed path is completely unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_httpx_response(vector: list[float], status_code: int = 200):
    """Build a minimal mock that looks like an httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {
        "data": [{"embedding": vector, "index": 0}],
        "model": "test-model",
        "object": "list",
    }
    mock.raise_for_status = MagicMock()
    return mock


def _litellm_env(**overrides):
    """Return a full valid LiteLLM embedding environment."""
    base = {
        "LITELLM_BASE_URL": "https://management.sprints.ai/litellm",
        "LITELLM_API_KEY": "sk-testkey",
        "LITELLM_EMBEDDING_MODEL": "text-embedding-3-small",
        "DENSE_EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
        "SPARSE_EMBEDDING_MODEL": "Qdrant/bm25",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. embed_dense uses the LiteLLM configuration
# ---------------------------------------------------------------------------

def test_embed_dense_calls_litellm_endpoint(monkeypatch):
    """embed_dense must POST to the LiteLLM /embeddings endpoint."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)

    expected_vector = [0.1, 0.2, 0.3]
    mock_response = _make_httpx_response(expected_vector)

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response) as mock_post:
        # Re-import with fresh EMBEDDING config
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        result = emb_mod.embed_dense("test input")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "/embeddings" in call_kwargs[0][0]
    assert result == expected_vector


# ---------------------------------------------------------------------------
# 2. API key and base URL are read from configuration
# ---------------------------------------------------------------------------

def test_embed_dense_sends_api_key_in_header(monkeypatch):
    """The Authorization header must carry the configured API key."""
    env = _litellm_env(LITELLM_API_KEY="sk-myspecialkey")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    mock_response = _make_httpx_response([0.5, 0.6])

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response) as mock_post:
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        emb_mod.embed_dense("hello")

    _, call_kwargs = mock_post.call_args
    headers = call_kwargs.get("headers", mock_post.call_args[1].get("headers", {}))
    assert headers.get("Authorization") == "Bearer sk-myspecialkey"


def test_embed_dense_posts_to_configured_base_url(monkeypatch):
    """The POST URL must be derived from LITELLM_BASE_URL."""
    env = _litellm_env(LITELLM_BASE_URL="https://custom.host/proxy")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    mock_response = _make_httpx_response([0.1])

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response) as mock_post:
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        emb_mod.embed_dense("hello")

    url_called = mock_post.call_args[0][0]
    assert url_called.startswith("https://custom.host/proxy")
    assert url_called.endswith("/embeddings")


# ---------------------------------------------------------------------------
# 3. Configured embedding model name is sent in the request body
# ---------------------------------------------------------------------------

def test_embed_dense_sends_configured_model_name(monkeypatch):
    """The JSON body must include the LITELLM_EMBEDDING_MODEL value."""
    env = _litellm_env(LITELLM_EMBEDDING_MODEL="my-custom-embed-model")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    mock_response = _make_httpx_response([0.9, 0.8])

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response) as mock_post:
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        emb_mod.embed_dense("some text")

    _, call_kwargs = mock_post.call_args
    body = call_kwargs.get("json", {})
    assert body.get("model") == "my-custom-embed-model"
    assert body.get("input") == "some text"


# ---------------------------------------------------------------------------
# 4. Returned dense vector is converted to list[float]
# ---------------------------------------------------------------------------

def test_embed_dense_returns_list_of_float(monkeypatch):
    """embed_dense must return list[float] even when the API returns mixed numeric types."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)

    # API may return ints or strings — they must all become float
    raw_vector = [1, 2, 3]
    mock_response = _make_httpx_response(raw_vector)

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response):
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        result = emb_mod.embed_dense("test")

    assert isinstance(result, list)
    assert all(isinstance(v, float) for v in result)


# ---------------------------------------------------------------------------
# 5. Missing configuration raises ValueError (not a silent failure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", [
    "LITELLM_BASE_URL",
    "LITELLM_API_KEY",
    "LITELLM_EMBEDDING_MODEL",
])
def test_embed_dense_raises_if_config_missing(monkeypatch, missing_key):
    """embed_dense must raise ValueError when a required env var is absent."""
    env = _litellm_env()
    env[missing_key] = ""  # Set to empty string — treated as missing
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    with patch("src.retrieval.embedding.httpx.post") as mock_post:
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        with pytest.raises(ValueError, match=missing_key.replace("_", ".")):
            emb_mod.embed_dense("anything")

    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Fingerprint behavior — reflects LiteLLM model when set
# ---------------------------------------------------------------------------

def test_get_model_fingerprint_uses_litellm_embedding_model(monkeypatch):
    """Fingerprint dense component must be LITELLM_EMBEDDING_MODEL when set."""
    env = _litellm_env(LITELLM_EMBEDDING_MODEL="text-embedding-3-small")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from importlib import reload
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    reload(cfg_mod)
    reload(emb_mod)

    fp = emb_mod.get_model_fingerprint()
    assert "text-embedding-3-small" in fp
    assert "Qdrant/bm25" in fp


def test_get_model_fingerprint_falls_back_to_dense_model_when_litellm_model_unset(monkeypatch):
    """Fingerprint falls back to DENSE_EMBEDDING_MODEL if LITELLM_EMBEDDING_MODEL is empty."""
    env = _litellm_env(LITELLM_EMBEDDING_MODEL="")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from importlib import reload
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    reload(cfg_mod)
    reload(emb_mod)

    fp = emb_mod.get_model_fingerprint()
    assert "BAAI/bge-small-en-v1.5" in fp


# ---------------------------------------------------------------------------
# 7. Dimension behavior — probes API or reads QDRANT_DENSE_DIMENSION override
# ---------------------------------------------------------------------------

def test_get_dense_dimension_from_env_override(monkeypatch):
    """QDRANT_DENSE_DIMENSION env var must bypass the API probe."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QDRANT_DENSE_DIMENSION", "512")

    with patch("src.retrieval.embedding.httpx.post") as mock_post:
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        dim = emb_mod.get_dense_dimension()

    assert dim == 512
    mock_post.assert_not_called()


def test_get_dense_dimension_probes_api_when_no_override(monkeypatch):
    """get_dense_dimension must call the API and return len(vector) when no env override."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QDRANT_DENSE_DIMENSION", "")

    probe_vector = [0.0] * 384
    mock_response = _make_httpx_response(probe_vector)

    with patch("src.retrieval.embedding.httpx.post", return_value=mock_response):
        from importlib import reload
        import src.config as cfg_mod
        import src.retrieval.embedding as emb_mod
        reload(cfg_mod)
        reload(emb_mod)

        dim = emb_mod.get_dense_dimension()

    assert dim == 384


def test_get_dense_dimension_rejects_invalid_env_override(monkeypatch):
    """QDRANT_DENSE_DIMENSION must be a valid integer or raise ValueError."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QDRANT_DENSE_DIMENSION", "notanumber")

    from importlib import reload
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    reload(cfg_mod)
    reload(emb_mod)

    with pytest.raises(ValueError, match="QDRANT_DENSE_DIMENSION"):
        emb_mod.get_dense_dimension()


# ---------------------------------------------------------------------------
# 5 (sparse). Sparse embeddings continue to work exactly as before
# ---------------------------------------------------------------------------

def test_embed_sparse_returns_indices_and_values(monkeypatch):
    """embed_sparse must return a dict with 'indices' and 'values' lists."""
    for k, v in _litellm_env().items():
        monkeypatch.setenv(k, v)

    from importlib import reload
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    reload(cfg_mod)
    reload(emb_mod)

    result = emb_mod.embed_sparse("network connectivity issue")

    assert isinstance(result, dict)
    assert "indices" in result
    assert "values" in result
    assert isinstance(result["indices"], list)
    assert isinstance(result["values"], list)
    assert len(result["indices"]) == len(result["values"])
    assert len(result["indices"]) > 0

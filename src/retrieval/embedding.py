"""Generate dense and sparse vectors for retrieval chunks.

Dense embeddings are produced via the LiteLLM-compatible embedding API
(LITELLM_BASE_URL / LITELLM_API_KEY / LITELLM_EMBEDDING_MODEL).

Sparse embeddings continue to use the local fastembed model (SPARSE_EMBEDDING_MODEL)
and are completely unchanged from the previous implementation.
"""

from __future__ import annotations

import os

import certifi

os.environ["SSL_CERT_FILE"] = certifi.where()

import httpx
from fastembed import SparseTextEmbedding

from ..config import EMBEDDING

# ---------------------------------------------------------------------------
# Sparse model — unchanged from original implementation
# ---------------------------------------------------------------------------
_sparse_model = SparseTextEmbedding(model_name=EMBEDDING.sparse_model)


# ---------------------------------------------------------------------------
# Dense embedding via LiteLLM API
# ---------------------------------------------------------------------------

def _litellm_embed(text: str) -> list[float]:
    """Call the LiteLLM embedding endpoint and return the vector as list[float].

    Raises ValueError if required configuration (base_url, api_key,
    embedding_model) is missing.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    base_url = EMBEDDING.litellm_base_url
    api_key = EMBEDDING.litellm_api_key
    model = EMBEDDING.litellm_embedding_model

    if not base_url:
        raise ValueError(
            "LITELLM_BASE_URL is not set. "
            "Add it to your .env file before using dense embeddings."
        )
    if not api_key:
        raise ValueError(
            "LITELLM_API_KEY is not set. "
            "Add it to your .env file before using dense embeddings."
        )
    if not model:
        raise ValueError(
            "LITELLM_EMBEDDING_MODEL is not set. "
            "Add it to your .env file before using dense embeddings."
        )

    url = base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "input": text}

    response = httpx.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    return [float(v) for v in data["data"][0]["embedding"]]


# ---------------------------------------------------------------------------
# Public API — same signatures as the original implementation
# ---------------------------------------------------------------------------

def embed_dense(text: str) -> list[float]:
    """Return a dense embedding vector for *text* using the LiteLLM API."""
    return _litellm_embed(text)


def embed_sparse(text: str) -> dict:
    """Return a sparse embedding dict for *text* using the local fastembed model.

    Unchanged from the original implementation.
    """
    result = list(_sparse_model.embed([text]))[0]
    return {
        "indices": result.indices.tolist(),
        "values": result.values.tolist(),
    }


def get_model_fingerprint() -> str:
    """Return a fingerprint string that identifies the active model pair.

    Used by the ingest pipeline to detect model mismatches on re-ingestion.
    The dense component now reflects the LiteLLM embedding model name so that
    switching models correctly invalidates the existing Qdrant collection.
    Falls back to EMBEDDING.dense_model if LITELLM_EMBEDDING_MODEL is not set.
    """
    dense_id = EMBEDDING.litellm_embedding_model or EMBEDDING.dense_model
    return f"{dense_id}::{EMBEDDING.sparse_model}"


def get_dense_dimension() -> int:
    """Return the dimensionality of the dense embedding vectors.

    **Limitation:** The LiteLLM API does not expose the embedding dimension
    without making an actual embedding request. This function obtains the
    dimension by embedding a short sentinel string. It will fail if the
    LiteLLM endpoint is unavailable or misconfigured.

    If you need the dimension at collection-creation time without a live API,
    set QDRANT_DENSE_DIMENSION in your environment to skip the probe call.
    """
    env_dim = os.environ.get("QDRANT_DENSE_DIMENSION", "").strip()
    if env_dim:
        try:
            return int(env_dim)
        except ValueError:
            raise ValueError(
                f"QDRANT_DENSE_DIMENSION must be a positive integer, got: {env_dim!r}"
            )
    vector = _litellm_embed("dimension probe")
    return len(vector)

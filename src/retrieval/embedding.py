"""
Generates dense + sparse vectors for a chunk of article text.
"""

import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from sentence_transformers import SentenceTransformer
from fastembed import SparseTextEmbedding

from ..config import EMBEDDING

_dense_model = SentenceTransformer(EMBEDDING.dense_model)
_sparse_model = SparseTextEmbedding(model_name=EMBEDDING.sparse_model)


def embed_dense(text: str) -> list[float]:
    return _dense_model.encode(text).tolist()


def embed_sparse(text: str) -> dict:
    result = list(_sparse_model.embed([text]))[0]
    return {
        "indices": result.indices.tolist(),
        "values": result.values.tolist(),
    }


def get_model_fingerprint() -> str:
    """Used to detect model mismatch on re-ingestion."""
    return f"{EMBEDDING.dense_model}::{EMBEDDING.sparse_model}"


def get_dense_dimension() -> int:
    return _dense_model.get_embedding_dimension()
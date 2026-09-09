"""
Generates dense + sparse vectors for a chunk of article text.
"""

import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from fastembed import SparseTextEmbedding

load_dotenv()

DENSE_MODEL_NAME = os.environ.get("DENSE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
SPARSE_MODEL_NAME = os.environ.get("SPARSE_EMBEDDING_MODEL", "Qdrant/bm25")

_dense_model = SentenceTransformer(DENSE_MODEL_NAME)
_sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)


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
    return f"{DENSE_MODEL_NAME}::{SPARSE_MODEL_NAME}"


def get_dense_dimension() -> int:
    return _dense_model.get_embedding_dimension()
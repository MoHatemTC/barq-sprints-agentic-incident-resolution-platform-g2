"""
Central, non-secret configuration for the retrieval pipeline.

Anything in this file is safe to commit and safe for any developer to read
or change directly -- these are defaults, not credentials. Real secrets
(ServiceNow OAuth client id/secret, username/password) stay in .env and
are never given defaults here, so a missing secret fails loudly instead
of silently falling back to something wrong.

Every value can still be overridden via an environment variable of the
same name, so CI or a different environment can change behavior without
editing this file.
"""

import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class QdrantConfig:
    url: str = os.environ.get("QDRANT_URL", "http://localhost:6333")
    collection_name: str = os.environ.get("QDRANT_COLLECTION_NAME", "barq_knowledge_base")


@dataclass(frozen=True)
class EmbeddingConfig:
    dense_model: str = os.environ.get("DENSE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    sparse_model: str = os.environ.get("SPARSE_EMBEDDING_MODEL", "Qdrant/bm25")


@dataclass(frozen=True)
class ServiceNowConfig:
    instance_url: str = os.environ.get("SERVICENOW_INSTANCE_URL", "").rstrip("/")
    kb_sys_id: str = os.environ.get("SERVICENOW_KB_SYS_ID", "")
    kb_table: str = os.environ.get("SERVICENOW_KB_TABLE", "kb_knowledge")
    # Secrets -- intentionally NO default. Missing values should fail
    # loudly in servicenow_auth.py, not silently authenticate as "".
    oauth_client_id: str = os.environ.get("SERVICENOW_OAUTH_CLIENT_ID", "")
    oauth_client_secret: str = os.environ.get("SERVICENOW_OAUTH_CLIENT_SECRET", "")
    oauth_username: str = os.environ.get("SERVICENOW_OAUTH_USERNAME", "")
    oauth_password: str = os.environ.get("SERVICENOW_OAUTH_PASSWORD", "")


@dataclass(frozen=True)
class PathsConfig:
    corpus_json: str = os.environ.get("CORPUS_JSON_PATH", "data/kb_dataset.json")
    coverage_matrix: str = os.environ.get("COVERAGE_MATRIX_PATH", "data/coverage_matrix.csv")
    servicenow_kb_mapping: str = os.environ.get(
        "SERVICENOW_KB_MAPPING_PATH", "data/servicenow_kb_mapping.json"
    )


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size: int = int(os.environ.get("CHUNK_SIZE", "500"))
    chunk_overlap: int = int(os.environ.get("CHUNK_OVERLAP", "50"))

    def __post_init__(self):
        if self.chunk_size <= 0:
            raise ValueError(f"CHUNK_SIZE must be > 0, got {self.chunk_size}")
        if not (0 <= self.chunk_overlap < self.chunk_size):
            raise ValueError(
                "CHUNK_OVERLAP must satisfy 0 <= CHUNK_OVERLAP < CHUNK_SIZE, "
                f"got CHUNK_OVERLAP={self.chunk_overlap}, CHUNK_SIZE={self.chunk_size}"
            )


QDRANT = QdrantConfig()
EMBEDDING = EmbeddingConfig()
SERVICENOW = ServiceNowConfig()
PATHS = PathsConfig()
CHUNKING = ChunkingConfig()
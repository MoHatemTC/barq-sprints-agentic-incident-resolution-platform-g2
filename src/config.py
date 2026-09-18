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

import json
import os
import re
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


_KB_METADATA_FIELDS = {"service", "version", "security_level", "article_number"}
_SERVICENOW_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _metadata_field_map() -> dict[str, str]:
    """Return canonical article field -> configured ServiceNow column name.

    The Knowledge table does not currently have the S1.4 metadata columns.
    Keeping this mapping opt-in prevents the publisher from guessing custom
    column names while letting the ServiceNow schema be wired in later.
    """
    raw = os.environ.get("SERVICENOW_KB_METADATA_FIELD_MAP", "{}")
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SERVICENOW_KB_METADATA_FIELD_MAP must be JSON") from exc

    if not isinstance(mapping, dict):
        raise ValueError("SERVICENOW_KB_METADATA_FIELD_MAP must be a JSON object")

    unknown = set(mapping) - _KB_METADATA_FIELDS
    if unknown:
        raise ValueError(
            "SERVICENOW_KB_METADATA_FIELD_MAP has unsupported source fields: "
            f"{sorted(unknown)}"
        )

    for source_field, servicenow_field in mapping.items():
        if not isinstance(servicenow_field, str) or not _SERVICENOW_FIELD_NAME.fullmatch(servicenow_field):
            raise ValueError(
                f"Invalid ServiceNow field name for {source_field}: {servicenow_field!r}"
            )
    return mapping


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
    # Optional, canonical corpus field -> actual kb_knowledge column mapping.
    # No defaults: the ServiceNow custom columns are owned by S1.1/S1.2.
    kb_metadata_field_map: dict[str, str] = field(default_factory=_metadata_field_map)
    # Optional approved publish action. It must be a relative instance path
    # containing {sys_id}, for example /api/x_scope/kb_publish/{sys_id}.
    # Empty means Table API publication must succeed by itself.
    kb_publish_action_path: str = os.environ.get("SERVICENOW_KB_PUBLISH_ACTION_PATH", "")
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
    servicenow_kb_category_mapping: str = os.environ.get(
        "SERVICENOW_KB_CATEGORY_MAPPING_PATH", "data/kb_category_mapping.json"
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


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    """Read a comma-separated env var into a tuple, e.g. "a, b" -> ("a", "b")."""
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# The three retrieval modes S2.4 compares. Switching between them is a
# config change only -- no code changes anywhere.
RETRIEVAL_MODES = ("dense", "hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class RetrievalConfig:
    """S2.4 query-side settings: mode switch, fusion, filters, reranking."""

    # Which retrieval pipeline to run. "dense" is the baseline we measure against.
    mode: str = os.environ.get("RETRIEVAL_MODE", "hybrid_rerank")
    # How many passages are finally returned (and passed to generation).
    top_k: int = int(os.environ.get("RETRIEVAL_TOP_K", "5"))
    # How many candidates each branch (dense / sparse) fetches BEFORE fusion
    # and reranking. Must be >= top_k.
    candidate_k: int = int(os.environ.get("RETRIEVAL_CANDIDATE_K", "20"))
    # Reciprocal Rank Fusion constant: score = sum(1 / (rrf_k + rank)).
    rrf_k: int = int(os.environ.get("RRF_K", "60"))
    # Cross-encoder used only in "hybrid_rerank" mode (fastembed model name).
    rerank_model: str = os.environ.get("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")

    # Metadata pre-filters (applied inside the search, before any ranking).
    # Only these workflow states may be retrieved ...
    allowed_workflow_states: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("ALLOWED_WORKFLOW_STATES", "published")
    )
    # ... and these security levels are never retrieved.
    blocked_security_levels: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("BLOCKED_SECURITY_LEVELS", "restricted")
    )

    # Latency budget (ms) that the ablation report compares p50/p95 against.
    latency_budget_ms: int = int(os.environ.get("LATENCY_BUDGET_MS", "500"))

    # Optional: run Qdrant embedded on disk instead of Docker (empty = use QDRANT_URL).
    qdrant_local_path: str = os.environ.get("QDRANT_LOCAL_PATH", "")

    # Track B: the service-desk manual gets its own collection and eval dataset.
    manual_collection_name: str = os.environ.get("MANUAL_COLLECTION_NAME", "barq_manual")
    manual_pdf_path: str = os.environ.get(
        "MANUAL_PDF_PATH", "data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf"
    )
    eval_dataset_path: str = os.environ.get(
        "EVAL_DATASET_PATH", "eval/barq_rag_eval_dataset.json"
    )

    def __post_init__(self):
        if self.mode not in RETRIEVAL_MODES:
            raise ValueError(
                f"RETRIEVAL_MODE must be one of {RETRIEVAL_MODES}, got {self.mode!r}"
            )
        if self.top_k <= 0:
            raise ValueError(f"RETRIEVAL_TOP_K must be > 0, got {self.top_k}")
        if self.candidate_k < self.top_k:
            raise ValueError(
                "RETRIEVAL_CANDIDATE_K must be >= RETRIEVAL_TOP_K, "
                f"got {self.candidate_k} < {self.top_k}"
            )
        if self.rrf_k <= 0:
            raise ValueError(f"RRF_K must be > 0, got {self.rrf_k}")
        if not self.allowed_workflow_states:
            raise ValueError("ALLOWED_WORKFLOW_STATES must list at least one state")


QDRANT = QdrantConfig()
EMBEDDING = EmbeddingConfig()
SERVICENOW = ServiceNowConfig()
PATHS = PathsConfig()
CHUNKING = ChunkingConfig()
RETRIEVAL = RetrievalConfig()

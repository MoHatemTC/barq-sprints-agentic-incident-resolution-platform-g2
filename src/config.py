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
from typing import Mapping

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
    litellm_base_url: str = os.environ.get("LITELLM_BASE_URL", "").strip()
    litellm_api_key: str = os.environ.get("LITELLM_API_KEY", "").strip()
    litellm_embedding_model: str = os.environ.get("LITELLM_EMBEDDING_MODEL", "").strip()


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


@dataclass(frozen=True)
class WorkerConfig:
    """Validated S2.3 worker settings, loaded only when a worker is configured."""

    broker_url: str
    main_queue: str
    dlq_queue: str
    worker_concurrency: int
    worker_prefetch_multiplier: int
    task_soft_time_limit_seconds: int
    task_time_limit_seconds: int
    task_max_retries: int
    retry_base_delay_seconds: int
    retry_max_delay_seconds: int
    task_acks_late: bool
    task_reject_on_worker_lost: bool
    worker_shutdown_timeout_seconds: int

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "WorkerConfig":
        """Build worker settings from an environment mapping."""
        env = os.environ if environment is None else environment
        config = cls(
            broker_url=_required_worker_text(env, "CELERY_BROKER_URL"),
            main_queue=_required_worker_text(env, "CELERY_MAIN_QUEUE"),
            dlq_queue=_required_worker_text(env, "CELERY_DLQ_QUEUE"),
            worker_concurrency=_required_worker_positive_int(
                env, "CELERY_WORKER_CONCURRENCY"
            ),
            worker_prefetch_multiplier=_required_worker_positive_int(
                env, "CELERY_WORKER_PREFETCH_MULTIPLIER"
            ),
            task_soft_time_limit_seconds=_required_worker_positive_int(
                env, "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS"
            ),
            task_time_limit_seconds=_required_worker_positive_int(
                env, "CELERY_TASK_TIME_LIMIT_SECONDS"
            ),
            task_max_retries=_required_worker_non_negative_int(
                env, "CELERY_TASK_MAX_RETRIES"
            ),
            retry_base_delay_seconds=_required_worker_non_negative_int(
                env, "CELERY_RETRY_BASE_DELAY_SECONDS"
            ),
            retry_max_delay_seconds=_required_worker_non_negative_int(
                env, "CELERY_RETRY_MAX_DELAY_SECONDS"
            ),
            task_acks_late=_required_worker_bool(env, "CELERY_TASK_ACKS_LATE"),
            task_reject_on_worker_lost=_required_worker_bool(
                env, "CELERY_TASK_REJECT_ON_WORKER_LOST"
            ),
            worker_shutdown_timeout_seconds=_required_worker_positive_int(
                env, "CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS"
            ),
        )
        if config.task_time_limit_seconds <= config.task_soft_time_limit_seconds:
            raise ValueError(
                "CELERY_TASK_TIME_LIMIT_SECONDS must be greater than "
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS"
            )
        if config.retry_max_delay_seconds < config.retry_base_delay_seconds:
            raise ValueError(
                "CELERY_RETRY_MAX_DELAY_SECONDS must be >= "
                "CELERY_RETRY_BASE_DELAY_SECONDS"
            )
        return config


def _required_worker_text(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _required_worker_non_negative_int(
    environment: Mapping[str, str],
    name: str,
) -> int:
    value = _required_worker_text(environment, name)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _required_worker_positive_int(
    environment: Mapping[str, str],
    name: str,
) -> int:
    parsed = _required_worker_non_negative_int(environment, name)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _required_worker_bool(environment: Mapping[str, str], name: str) -> bool:
    value = _required_worker_text(environment, name).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be true or false")


QDRANT = QdrantConfig()
EMBEDDING = EmbeddingConfig()
SERVICENOW = ServiceNowConfig()
PATHS = PathsConfig()
CHUNKING = ChunkingConfig()

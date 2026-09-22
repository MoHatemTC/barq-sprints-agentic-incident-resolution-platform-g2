"""S2.3 dead-letter evidence records and configured Redis-list publishing."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Protocol


@dataclass(frozen=True)
class DeadLetterEntry:
    """S2.3 evidence retained for one terminal worker task attempt.

    ``execution_context`` is intentionally optional and injected.  The
    confirmed S2.1 payload contains no S2.2 execution identifier, so it must
    never be derived from payload fields here.
    """

    payload: object
    execution_context: object | None
    error_type: str
    error_message: str
    retry_count: int
    task_name: str
    task_id: str | None
    occurred_at: datetime


class RedisListPublisher(Protocol):
    def rpush(self, key: str, value: str) -> object:
        """Append one serialized dead-letter record to the configured list."""


@dataclass(frozen=True)
class RedisListDlq:
    """Concrete S2.3 DLQ sink for the configured Redis list."""

    redis_client: RedisListPublisher
    queue_name: str

    def transition(self, entry: DeadLetterEntry) -> None:
        self.redis_client.rpush(self.queue_name, serialize_dead_letter_entry(entry))


def create_dead_letter_entry(
    *,
    payload: object,
    error: BaseException,
    retry_count: int,
    task_name: str,
    task_id: str | None,
    execution_context: object | None = None,
    occurred_at: datetime | None = None,
) -> DeadLetterEntry:
    """Create a timezone-aware, transport-neutral dead-letter record."""
    if payload is None:
        raise ValueError("payload must not be None")
    if retry_count < 0:
        raise ValueError("retry_count must be non-negative")
    if not task_name:
        raise ValueError("task_name must not be empty")

    timestamp = occurred_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")

    return DeadLetterEntry(
        payload=payload,
        execution_context=execution_context,
        error_type=type(error).__name__,
        error_message=str(error),
        retry_count=retry_count,
        task_name=task_name,
        task_id=task_id,
        occurred_at=timestamp,
    )


def serialize_dead_letter_entry(entry: DeadLetterEntry) -> str:
    """Serialize complete S2.3 DLQ evidence without defining a Celery route."""
    return json.dumps(
        {
            "payload": entry.payload,
            "execution_context": _serialize_context(entry.execution_context),
            "error_type": entry.error_type,
            "error_message": entry.error_message,
            "retry_count": entry.retry_count,
            "task_name": entry.task_name,
            "task_id": entry.task_id,
            "occurred_at": entry.occurred_at.isoformat(),
        },
        default=_json_fallback,
        separators=(",", ":"),
    )


def _serialize_context(context: object | None) -> object | None:
    if context is None:
        return None
    execution_identifier = getattr(context, "execution_identifier", None)
    retry_state_id = getattr(context, "retry_state_id", None)
    if isinstance(execution_identifier, str) and isinstance(retry_state_id, int):
        return {
            "execution_identifier": execution_identifier,
            "retry_state_id": retry_state_id,
        }
    return context


def _json_fallback(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"DLQ value is not JSON serializable: {type(value).__name__}")

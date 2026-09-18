"""S2.3 transport-neutral dead-letter evidence records.

The queue publisher remains an injected boundary until S2.1 publishes the
producer/routing contract.  These values are deliberately transport-neutral so
that a transition can retain the same evidence regardless of its eventual
Redis implementation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


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

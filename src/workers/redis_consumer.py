"""S2.3 bridge from S2.1's Redis list to the worker task boundary.

S2.1 intentionally publishes JSON with ``RPUSH incident_events``.  This
module consumes that list with blocking ``BLPOP`` and invokes the existing
S2.3 task so retry, timeout, and dead-letter decisions remain in ``tasks``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol


INCIDENT_EVENTS_LIST = "incident_events"
_REQUIRED_INCIDENT_FIELDS = ("sys_id", "event_id", "event_type", "number")

logger = logging.getLogger(__name__)


class RedisListClient(Protocol):
    """The small synchronous Redis surface used by this consumer."""

    def blpop(self, keys: str, timeout: int = 0) -> object | None:
        """Block until one list entry is available or the timeout expires."""


class TaskInvoker(Protocol):
    """Existing Celery task invocation surface used by the consumer."""

    def apply(self, args: tuple[object, ...]) -> object:
        """Invoke the existing S2.3 task orchestration for one payload."""


class CeleryTaskInvoker(TaskInvoker, Protocol):
    def apply_async(
        self,
        args: tuple[object, ...],
        headers: Mapping[str, str | int] | None = None,
    ) -> object:
        """Submit one task to the S2.3 Celery worker with internal headers."""


@dataclass(frozen=True)
class MalformedIncidentPayload:
    """Internal payload/error pair for the task's terminal failure path."""

    original_payload: object
    error: ValueError


def deserialize_incident_message(message: object) -> object:
    """Decode and validate one S2.1 list value without changing valid payloads."""
    raw_message = _decode_message(message)
    if isinstance(raw_message, MalformedIncidentPayload):
        return raw_message

    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError as error:
        return MalformedIncidentPayload(
            original_payload=raw_message,
            error=ValueError(f"Malformed incident JSON: {error.msg}"),
        )

    validation_error = _payload_validation_error(payload)
    if validation_error is not None:
        return MalformedIncidentPayload(
            original_payload=raw_message,
            error=validation_error,
        )
    return payload


def consume_next_incident(
    redis_client: RedisListClient,
    process_task: TaskInvoker,
    *,
    block_timeout_seconds: int = 5,
) -> bool:
    """Block for and submit one incident-list entry to S2.3 orchestration.

    Returns ``False`` only when the blocking read times out. A task failure is
    intentionally isolated: the task owns retry/DLQ handling and this function
    has already removed the one bad list entry, so later calls can process
    healthy events.
    """
    if block_timeout_seconds <= 0:
        raise ValueError("block_timeout_seconds must be positive")

    item = redis_client.blpop(INCIDENT_EVENTS_LIST, timeout=block_timeout_seconds)
    if item is None:
        return False

    try:
        _, message = _unpack_redis_item(item)
    except ValueError:
        logger.exception("Redis incident consumer received an invalid list response")
        return True

    process_task.apply(args=(deserialize_incident_message(message),))
    return True


def consume_next_incident_for_celery(
    redis_client: RedisListClient,
    process_task: CeleryTaskInvoker,
    establish_context: Callable[[Mapping[str, object]], object],
    *,
    block_timeout_seconds: int = 5,
) -> bool:
    """Dispatch one list message to Celery with S2.2 context in task headers."""
    if block_timeout_seconds <= 0:
        raise ValueError("block_timeout_seconds must be positive")
    item = redis_client.blpop(INCIDENT_EVENTS_LIST, timeout=block_timeout_seconds)
    if item is None:
        return False

    try:
        _, message = _unpack_redis_item(item)
    except ValueError:
        logger.exception("Redis incident consumer received an invalid list response")
        return True

    payload = deserialize_incident_message(message)
    headers = None
    if isinstance(payload, Mapping):
        context = establish_context(payload)
        task_headers = getattr(context, "task_headers", None)
        if not callable(task_headers):
            raise TypeError("execution context must provide task_headers()")
        headers = task_headers()
    process_task.apply_async(args=(payload,), headers=headers)
    return True


def run_incident_consumer_for_celery(
    redis_client: RedisListClient,
    process_task: CeleryTaskInvoker,
    establish_context: Callable[[Mapping[str, object]], object],
    should_stop: Callable[[], bool],
    *,
    block_timeout_seconds: int = 5,
) -> None:
    """Run the production Redis-list-to-Celery dispatch loop."""
    while not should_stop():
        try:
            consume_next_incident_for_celery(
                redis_client,
                process_task,
                establish_context,
                block_timeout_seconds=block_timeout_seconds,
            )
        except Exception:
            logger.exception("Redis-to-Celery incident dispatch iteration failed")


def _decode_message(message: object) -> str | MalformedIncidentPayload:
    if isinstance(message, bytes):
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError as error:
            return MalformedIncidentPayload(
                original_payload=message,
                error=ValueError(f"Malformed incident JSON encoding: {error.reason}"),
            )
    if isinstance(message, str):
        return message
    return MalformedIncidentPayload(
        original_payload=message,
        error=ValueError("Malformed incident JSON: Redis list value must be text"),
    )


def _payload_validation_error(payload: object) -> ValueError | None:
    if not isinstance(payload, Mapping):
        return ValueError("Malformed incident payload: expected a JSON object")

    for field in _REQUIRED_INCIDENT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return ValueError(
                f"Malformed incident payload: {field} must be a non-empty string"
            )
    return None


def _unpack_redis_item(item: object) -> tuple[object, object]:
    if not isinstance(item, tuple) or len(item) != 2:
        raise ValueError("Redis BLPOP response must be a queue/message pair")
    return item

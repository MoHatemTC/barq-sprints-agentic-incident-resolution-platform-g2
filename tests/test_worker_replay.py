from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from src.workers.dlq import create_dead_letter_entry
from src.workers.replay import replay_dlq_payload


@dataclass
class RecordingProcessor:
    """TEST-ONLY processor spy with no worker retry or DLQ behavior."""

    result: object = None
    error: BaseException | None = None
    received_payloads: list[object] = field(default_factory=list)

    def __call__(self, payload: object) -> object:
        self.received_payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


def test_replay_returns_processing_result_and_preserves_payload_identity():
    payload = {"opaque": ["event", {"context": "preserved"}]}
    processor = RecordingProcessor(result={"status": "replayed"})

    result = replay_dlq_payload(payload, processor)

    assert result == {"status": "replayed"}
    assert len(processor.received_payloads) == 1
    assert processor.received_payloads[0] is payload


def test_replay_propagates_processing_failure_unchanged():
    payload = object()
    error = RuntimeError("processing failed")
    processor = RecordingProcessor(error=error)

    with pytest.raises(RuntimeError) as raised:
        replay_dlq_payload(payload, processor)

    assert raised.value is error
    assert len(processor.received_payloads) == 1
    assert processor.received_payloads[0] is payload


@pytest.mark.parametrize("payload", [None])
def test_replay_rejects_missing_payload(payload: object | None):
    with pytest.raises(ValueError, match="payload must not be None"):
        replay_dlq_payload(payload, RecordingProcessor())


def test_replay_rejects_non_callable_processor():
    with pytest.raises(TypeError, match="process must be callable"):
        replay_dlq_payload(object(), object())  # type: ignore[arg-type]


def test_replay_preserves_dead_letter_entry_without_orchestration_side_effects():
    payload = {"event_id": "event-1", "number": "INC0010001"}
    execution_context = {"execution_reference": "s2-2-generated-id"}
    entry = create_dead_letter_entry(
        payload=payload,
        execution_context=execution_context,
        error=RuntimeError("root cause fixed"),
        retry_count=2,
        task_name="test.worker",
        task_id="task-1",
        occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    processor = RecordingProcessor(result="replayed")

    result = replay_dlq_payload(entry, processor)

    assert result == "replayed"
    assert processor.received_payloads == [entry]
    assert processor.received_payloads[0] is entry
    assert entry.payload is payload
    assert entry.execution_context is execution_context

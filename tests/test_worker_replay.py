from dataclasses import dataclass, field

import pytest

from src.workers.replay import replay_dlq_payload


@dataclass
class RecordingProcessor:
    """TEST-ONLY processor spy with no worker retry or DLQ behavior."""

    result: object = None
    error: BaseException | None = None
    received_payloads: list[object] = field(default_factory=list)
    retry_calls: list[object] = field(default_factory=list)
    dlq_transitions: list[object] = field(default_factory=list)

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


def test_replay_does_not_perform_retry_or_dlq_behavior():
    processor = RecordingProcessor(result="processed")

    assert replay_dlq_payload(object(), processor) == "processed"
    assert processor.retry_calls == []
    assert processor.dlq_transitions == []


@pytest.mark.parametrize("payload", [None])
def test_replay_rejects_missing_payload(payload: object | None):
    with pytest.raises(ValueError, match="payload must not be None"):
        replay_dlq_payload(payload, RecordingProcessor())


def test_replay_rejects_non_callable_processor():
    with pytest.raises(TypeError, match="process must be callable"):
        replay_dlq_payload(object(), object())  # type: ignore[arg-type]

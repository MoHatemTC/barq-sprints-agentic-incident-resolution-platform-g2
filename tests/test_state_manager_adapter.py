"""Tests for S2.3's adapter to published S2.2 StateManager methods."""

from dataclasses import dataclass, field

import pytest

from src.workers.state_manager_adapter import (
    StateManagerRecorder,
    create_retry_state_operation,
    execution_reference_from_execution,
    update_retry_state_operation,
)
from tests.worker_test_doubles import (
    AcceptedIncidentFixture,
    RetryableAgentFailure,
    TerminalAgentFailure,
)


@dataclass(frozen=True)
class _Execution:
    execution_identifier: str


@dataclass
class _RecordingStateManager:
    """TEST-ONLY S2.2-compatible StateManager double."""

    create_retry_calls: list[tuple[object, ...]] = field(default_factory=list)
    update_retry_calls: list[tuple[object, ...]] = field(default_factory=list)
    failure_calls: list[tuple[object, ...]] = field(default_factory=list)

    def create_retry_state(
        self,
        execution_reference: str,
        attempt_count: int,
        last_error: str | None = None,
        next_attempt_time: object | None = None,
    ) -> object:
        self.create_retry_calls.append(
            (execution_reference, attempt_count, last_error, next_attempt_time)
        )
        return object()

    def update_retry_state(
        self,
        retry_state_id: int,
        attempt_count: int,
        last_error: str | None = None,
        next_attempt_time: object | None = None,
    ) -> object:
        self.update_retry_calls.append(
            (retry_state_id, attempt_count, last_error, next_attempt_time)
        )
        return object()

    def record_failure(
        self,
        execution_reference: str,
        failing_node: str,
        error_class: str,
        message: str,
        retry_count: int = 0,
    ) -> object:
        self.failure_calls.append(
            (
                execution_reference,
                failing_node,
                error_class,
                message,
                retry_count,
            )
        )
        return object()


def _incident() -> AcceptedIncidentFixture:
    return AcceptedIncidentFixture(
        sys_id="a1b2c3d4e5f678901234567890abcdef",
        event_id="evt_9f8e7d6c5b4a3210",
        event_type="Insert",
        number="INC0012345",
    )


def test_execution_reference_comes_from_s2_2_execution_identifier():
    execution_reference = execution_reference_from_execution(
        _Execution("execution-generated-by-s2-2")
    )

    assert execution_reference == "execution-generated-by-s2-2"


@pytest.mark.parametrize("identifier", [None, "", "   "])
def test_execution_reference_rejects_missing_s2_2_execution_identifier(identifier):
    with pytest.raises(ValueError, match="execution.execution_identifier"):
        execution_reference_from_execution(_Execution(identifier))


def test_recorder_delegates_create_retry_state_when_selected():
    state_manager = _RecordingStateManager()
    recorder = StateManagerRecorder(
        state_manager=state_manager,
        execution_reference="execution-generated-by-s2-2",
        failing_node="pending-worker-boundary",
        retry_state_operation=create_retry_state_operation,
    )
    error = RetryableAgentFailure("temporary")

    recorder.record_retry(_incident(), retries_completed=0, error=error)

    assert state_manager.create_retry_calls == [
        ("execution-generated-by-s2-2", 1, "temporary", None)
    ]
    assert state_manager.update_retry_calls == []


def test_recorder_delegates_update_retry_state_when_selected():
    state_manager = _RecordingStateManager()
    recorder = StateManagerRecorder(
        state_manager=state_manager,
        execution_reference="execution-generated-by-s2-2",
        failing_node="pending-worker-boundary",
        retry_state_operation=update_retry_state_operation(retry_state_id=7),
    )
    error = RetryableAgentFailure("temporary")

    recorder.record_retry(_incident(), retries_completed=1, error=error)

    assert state_manager.create_retry_calls == []
    assert state_manager.update_retry_calls == [(7, 2, "temporary", None)]


@pytest.mark.parametrize(
    ("error", "retries_completed"),
    [
        (TerminalAgentFailure("invalid"), 0),
        (RetryableAgentFailure("exhausted"), 2),
    ],
)
def test_recorder_delegates_terminal_or_exhausted_failure_to_state_manager(
    error,
    retries_completed,
):
    state_manager = _RecordingStateManager()
    recorder = StateManagerRecorder(
        state_manager=state_manager,
        execution_reference="execution-generated-by-s2-2",
        failing_node="pending-worker-boundary",
        retry_state_operation=create_retry_state_operation,
    )

    recorder.record_failure(
        _incident(),
        retries_completed=retries_completed,
        error=error,
    )

    assert state_manager.failure_calls == [
        (
            "execution-generated-by-s2-2",
            "pending-worker-boundary",
            type(error).__name__,
            str(error),
            retries_completed,
        )
    ]

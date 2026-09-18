"""Tests for the narrow, injection-only S2.3 StateManager adapter."""

from dataclasses import dataclass, field

from src.workers.state_manager_adapter import StateManagerRecorder
from tests.worker_test_doubles import (
    AcceptedIncidentFixture,
    RetryableAgentFailure,
    TerminalAgentFailure,
)


@dataclass
class _RecordingStateManager:
    """TEST-ONLY/PENDING S2.2 AGREEMENT StateManager-compatible double."""

    failure_calls: list[tuple[object, ...]] = field(default_factory=list)

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


@dataclass
class _RecordingRetryPersistence:
    """TEST-ONLY/PENDING S2.2 AGREEMENT opaque retry persistence seam."""

    retry_calls: list[tuple[object, ...]] = field(default_factory=list)

    def record_retry(
        self,
        execution_reference: str,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        self.retry_calls.append((execution_reference, retries_completed, error))


def _incident() -> AcceptedIncidentFixture:
    return AcceptedIncidentFixture(
        sys_id="a1b2c3d4e5f678901234567890abcdef",
        event_id="evt_9f8e7d6c5b4a3210",
        event_type="Insert",
        number="INC0012345",
    )


def test_adapter_uses_explicit_execution_reference_not_payload_fields():
    state_manager = _RecordingStateManager()
    retry_persistence = _RecordingRetryPersistence()
    adapter = StateManagerRecorder(
        state_manager=state_manager,
        retry_persistence=retry_persistence,
        execution_reference="execution-reference-from-future-contract",
        failing_node="future-worker-boundary",
    )
    retry_error = RetryableAgentFailure("temporary")
    failure_error = TerminalAgentFailure("invalid")

    adapter.record_retry(_incident(), retries_completed=1, error=retry_error)
    adapter.record_failure(
        _incident(),
        retries_completed=2,
        error=failure_error,
    )

    assert retry_persistence.retry_calls == [
        ("execution-reference-from-future-contract", 1, retry_error)
    ]
    assert state_manager.failure_calls == [
        (
            "execution-reference-from-future-contract",
            "future-worker-boundary",
            "TerminalAgentFailure",
            "invalid",
            2,
        )
    ]

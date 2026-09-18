"""TEST-ONLY/PENDING TEAM AGREEMENT integration doubles for future S2.3 tests.

These types do not define production S2.1, S2.2, or S2.5 interfaces.
They are replaceable test fixtures used to exercise S2.3 orchestration behavior
before the owning teams publish their contracts.
"""

from dataclasses import dataclass, field
from typing import Protocol

from src.workers.dlq import DeadLetterEntry


@dataclass(frozen=True)
class AcceptedIncidentFixture:
    """TEST-ONLY fixture matching the confirmed S2.1 webhook payload shape."""

    event_id: str
    sys_id: str
    number: str
    event_type: str


class TestOnlyAgentExecutor(Protocol):
    """TEST-ONLY/PENDING S2.5 AGREEMENT agent invocation shape."""

    def execute(self, incident: AcceptedIncidentFixture) -> object:
        """Execute one accepted incident or raise a simulated failure."""


@dataclass
class StubAgentExecutor:
    """Configurable TEST-ONLY agent stub for worker-boundary tests."""

    result: object = None
    error: BaseException | None = None
    received_incidents: list[AcceptedIncidentFixture] = field(default_factory=list)

    def execute(self, incident: AcceptedIncidentFixture) -> object:
        self.received_incidents.append(incident)
        if self.error is not None:
            raise self.error
        return self.result


class RetryableAgentFailure(Exception):
    """TEST-ONLY failure that follows the existing retryable convention."""

    retryable = True


class TerminalAgentFailure(Exception):
    """TEST-ONLY failure that follows the existing retryable convention."""

    retryable = False


class SimulatedTaskTimeout(Exception):
    """TEST-ONLY timeout whose retryability is chosen by the test."""

    def __init__(self, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("simulated task timeout")


@dataclass(frozen=True)
class RecordedRetry:
    """TEST-ONLY observed retry-state write."""

    incident: AcceptedIncidentFixture
    retries_completed: int
    error: BaseException


@dataclass(frozen=True)
class RecordedFailure:
    """TEST-ONLY observed terminal or exhausted failure write."""

    incident: AcceptedIncidentFixture
    error: BaseException


@dataclass
class RecordingStateRecorder:
    """TEST-ONLY/PENDING S2.2 AGREEMENT retry/failure recorder."""

    retries: list[RecordedRetry] = field(default_factory=list)
    failures: list[RecordedFailure] = field(default_factory=list)

    def record_retry(
        self,
        incident: AcceptedIncidentFixture,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        self.retries.append(RecordedRetry(incident, retries_completed, error))

    def record_failure(
        self,
        incident: AcceptedIncidentFixture,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        self.failures.append(RecordedFailure(incident, error))


@dataclass
class RecordingDlq:
    """TEST-ONLY/PENDING S2.1 AGREEMENT DLQ sink with no Redis behavior."""

    transitions: list[DeadLetterEntry] = field(default_factory=list)

    def transition(self, entry: DeadLetterEntry) -> None:
        self.transitions.append(entry)

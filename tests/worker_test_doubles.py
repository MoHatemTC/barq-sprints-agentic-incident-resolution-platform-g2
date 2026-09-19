"""TEST-ONLY/PENDING TEAM AGREEMENT integration doubles for future S2.3 tests.

These types do not define production S2.1, S2.2, or S2.5 interfaces.
They are replaceable test fixtures used to exercise S2.3 orchestration behavior
before the owning teams publish their contracts.
"""

from dataclasses import dataclass, field

from src.workers.dlq import DeadLetterEntry


@dataclass(frozen=True)
class AcceptedIncidentFixture:
    """TEST-ONLY fixture matching the confirmed S2.1 webhook payload shape."""

    event_id: str
    sys_id: str
    number: str
    event_type: str


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


def replay_dlq_payload(payload: object, process: object) -> object:
    """Pass one opaque DLQ payload to the supplied processing callable.

    Moved from src/workers/replay.py — this is test infrastructure
    demonstrating the replay requirement, not a production module.
    """
    if payload is None:
        raise ValueError("payload must not be None")
    if not callable(process):
        raise TypeError("process must be callable")
    return process(payload)

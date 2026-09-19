"""Tests for TEST-ONLY/PENDING TEAM AGREEMENT worker integration doubles."""

import pytest

from src.workers.dlq import create_dead_letter_entry
from tests.worker_test_doubles import (
    RecordingDlq,
    RecordingStateRecorder,
    RetryableAgentFailure,
    StubAgentExecutor,
    TerminalAgentFailure,
    AcceptedIncidentFixture,
)


def _incident() -> AcceptedIncidentFixture:
    return AcceptedIncidentFixture(
        event_id="event-1",
        sys_id="incident-1",
        number="INC0010001",
        event_type="Insert",
    )


def test_agent_stub_records_accepted_incident_and_returns_configured_result():
    incident = _incident()
    agent = StubAgentExecutor(result={"status": "complete"})

    assert agent.execute(incident) == {"status": "complete"}
    assert agent.received_incidents == [incident]


def test_agent_stub_raises_configured_failure_after_recording_incident():
    incident = _incident()
    failure = RetryableAgentFailure("temporary failure")
    agent = StubAgentExecutor(error=failure)

    with pytest.raises(RetryableAgentFailure):
        agent.execute(incident)

    assert agent.received_incidents == [incident]


def test_failure_doubles_expose_test_controlled_retryability():
    assert RetryableAgentFailure("temporary").retryable is True
    assert TerminalAgentFailure("invalid").retryable is False


def test_recording_state_recorder_preserves_retry_and_failure_context():
    incident = _incident()
    retry_error = RetryableAgentFailure("temporary")
    failure_error = TerminalAgentFailure("invalid")
    recorder = RecordingStateRecorder()

    recorder.record_retry(incident, retries_completed=1, error=retry_error)
    recorder.record_failure(
        incident,
        retries_completed=2,
        error=failure_error,
    )

    assert recorder.retries[0].incident == incident
    assert recorder.retries[0].retries_completed == 1
    assert recorder.retries[0].error is retry_error
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is failure_error


def test_recording_dlq_preserves_incident_and_failure_context():
    incident = _incident()
    failure = TerminalAgentFailure("invalid")
    dlq = RecordingDlq()

    dlq.transition(
        create_dead_letter_entry(
            payload=incident,
            error=failure,
            retry_count=0,
            task_name="test.task",
            task_id=None,
        )
    )

    assert dlq.transitions[0].payload is incident
    assert dlq.transitions[0].error_type == type(failure).__name__
    assert dlq.transitions[0].error_message == str(failure)

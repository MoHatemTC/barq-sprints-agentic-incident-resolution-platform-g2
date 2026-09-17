from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded

from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app
from src.workers.retry_policy import RetryPolicy
from src.workers.tasks import (
    PendingIntegrationSeamsForTests,
    register_process_accepted_incident_task,
)
from tests.worker_test_doubles import (
    AcceptedIncidentFixture,
    RecordingDlq,
    RecordingStateRecorder,
    RetryableAgentFailure,
    StubAgentExecutor,
    TerminalAgentFailure,
)


class UnexpectedRetryableProcessingFailure(Exception):
    """TEST-ONLY/PENDING TEAM AGREEMENT unexpected retryable failure marker.

    This uses the existing RetryPolicy ``retryable`` convention and does not
    establish an S2.5 production exception contract.
    """

    retryable = True


class UnexpectedProcessingFailure(Exception):
    """TEST-ONLY/PENDING TEAM AGREEMENT unclassified unexpected failure."""


def _task(
    agent: StubAgentExecutor,
    *,
    max_retries: int = 2,
    shutdown_timeout_seconds: int = 60,
):
    app = create_celery_app(
        WorkerConfig(
            broker_url="redis://redis:6379/0",
            main_queue="test-main",
            dlq_queue="test-dlq",
            worker_concurrency=1,
            worker_prefetch_multiplier=1,
            task_soft_time_limit_seconds=30,
            task_time_limit_seconds=45,
            task_max_retries=max_retries,
            retry_base_delay_seconds=5,
            retry_max_delay_seconds=60,
            task_acks_late=True,
            task_reject_on_worker_lost=True,
            worker_shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
    )
    recorder = RecordingStateRecorder()
    dlq = RecordingDlq()
    task = register_process_accepted_incident_task(
        RetryPolicy(
            base_delay_seconds=5,
            max_delay_seconds=60,
            max_retries=max_retries,
        ),
        PendingIntegrationSeamsForTests(
            agent=agent,
            state_recorder=recorder,
            dlq=dlq,
        ),
        app=app,
    )
    return task, recorder, dlq


def _incident() -> AcceptedIncidentFixture:
    return AcceptedIncidentFixture(
        event_id="event-1",
        sys_id="incident-1",
        number="INC0010001",
        event_type="Insert",
    )


def test_task_accepts_confirmed_s2_1_incident_webhook_payload_shape():
    incident = AcceptedIncidentFixture(
        sys_id="a1b2c3d4e5f678901234567890abcdef",
        event_id="evt_9f8e7d6c5b4a3210",
        event_type="Insert",
        number="INC0012345",
    )
    agent = StubAgentExecutor(result={"status": "complete"})
    task, recorder, dlq = _task(agent)

    result = task.apply(args=(incident,), throw=True)

    assert result.result == {"status": "complete"}
    assert agent.received_incidents == [incident]
    assert recorder.retries == []
    assert recorder.failures == []
    assert dlq.transitions == []


def test_task_returns_agent_result_on_success():
    incident = _incident()
    task, recorder, dlq = _task(StubAgentExecutor(result={"status": "complete"}))

    result = task.apply(args=(incident,), throw=True)

    assert result.result == {"status": "complete"}
    assert task.request.retries == 0
    assert recorder.retries == []
    assert recorder.failures == []
    assert dlq.transitions == []


def test_task_retries_retryable_failure_with_policy_delay():
    incident = _incident()
    error = RetryableAgentFailure("temporary")
    task, recorder, dlq = _task(StubAgentExecutor(error=error))

    with patch.object(task, "retry", return_value="retry-scheduled") as retry:
        result = task.apply(args=(incident,), retries=0, throw=True)

    assert result.result == "retry-scheduled"
    retry.assert_called_once_with(exc=error, countdown=5, max_retries=2)
    assert len(recorder.retries) == 1
    assert recorder.retries[0].incident == incident
    assert recorder.retries[0].retries_completed == 0
    assert recorder.retries[0].error is error
    assert len(recorder.failures) == 0
    assert dlq.transitions == []


def test_task_dead_letters_exhausted_retryable_failure():
    incident = _incident()
    error = RetryableAgentFailure("temporary")
    task, recorder, dlq = _task(StubAgentExecutor(error=error), max_retries=2)

    result = task.apply(args=(incident,), retries=2)

    assert result.state == "FAILURE"
    assert result.result is error
    assert len(recorder.retries) == 0
    assert len(recorder.failures) == 1
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is error
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_task_dead_letters_terminal_failure_without_retry():
    incident = _incident()
    error = TerminalAgentFailure("invalid")
    task, recorder, dlq = _task(StubAgentExecutor(error=error))

    result = task.apply(args=(incident,), retries=0)

    assert result.state == "FAILURE"
    assert result.result is error
    assert recorder.retries == []
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is error
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_task_retries_soft_timeout_with_policy_delay():
    incident = _incident()
    error = SoftTimeLimitExceeded()
    task, recorder, dlq = _task(StubAgentExecutor(error=error))

    with patch.object(task, "retry", return_value="retry-scheduled") as retry:
        result = task.apply(args=(incident,), retries=0, throw=True)

    assert result.result == "retry-scheduled"
    retry.assert_called_once_with(exc=error, countdown=5, max_retries=2)
    assert recorder.retries[0].incident == incident
    assert recorder.retries[0].retries_completed == 0
    assert recorder.retries[0].error is error
    assert recorder.failures == []
    assert dlq.transitions == []


def test_task_dead_letters_exhausted_soft_timeout_once():
    incident = _incident()
    error = SoftTimeLimitExceeded()
    task, recorder, dlq = _task(StubAgentExecutor(error=error), max_retries=2)

    result = task.apply(args=(incident,), retries=2)

    assert result.state == "FAILURE"
    assert result.result is error
    assert recorder.retries == []
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is error
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_task_isolates_unexpected_retryable_failure_without_terminating_worker():
    incident = _incident()
    error = UnexpectedRetryableProcessingFailure("unexpected temporary failure")
    task, recorder, dlq = _task(StubAgentExecutor(error=error))

    with patch.object(task, "retry", return_value="retry-scheduled") as retry:
        result = task.apply(args=(incident,), retries=0, throw=True)

    assert result.result == "retry-scheduled"
    retry.assert_called_once_with(exc=error, countdown=5, max_retries=2)
    assert len(recorder.retries) == 1
    assert len(recorder.failures) == 0
    assert dlq.transitions == []

    healthy_task, healthy_recorder, healthy_dlq = _task(
        StubAgentExecutor(result="healthy")
    )
    healthy_result = healthy_task.apply(args=(_incident(),), throw=True)

    assert healthy_result.result == "healthy"
    assert healthy_recorder.retries == []
    assert healthy_recorder.failures == []
    assert healthy_dlq.transitions == []


def test_task_dead_letters_unclassified_unexpected_failure_as_terminal():
    incident = _incident()
    error = UnexpectedProcessingFailure("unexpected failure")
    task, recorder, dlq = _task(StubAgentExecutor(error=error))

    result = task.apply(args=(incident,), retries=0)

    assert result.state == "FAILURE"
    assert result.result is error
    assert len(recorder.retries) == 0
    assert len(recorder.failures) == 1
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is error
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_task_dead_letters_exhausted_unexpected_retryable_failure_once():
    incident = _incident()
    error = UnexpectedRetryableProcessingFailure("unexpected temporary failure")
    task, recorder, dlq = _task(StubAgentExecutor(error=error), max_retries=2)

    result = task.apply(args=(incident,), retries=2)

    assert result.state == "FAILURE"
    assert result.result is error
    assert len(recorder.retries) == 0
    assert len(recorder.failures) == 1
    assert recorder.failures[0].incident == incident
    assert recorder.failures[0].error is error
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_shutdown_timeout_does_not_change_task_retry_behavior():
    incident = _incident()
    error = RetryableAgentFailure("temporary")
    task, recorder, dlq = _task(
        StubAgentExecutor(error=error),
        shutdown_timeout_seconds=120,
    )

    with patch.object(task, "retry", return_value="retry-scheduled") as retry:
        result = task.apply(args=(incident,), retries=0, throw=True)

    assert result.result == "retry-scheduled"
    retry.assert_called_once_with(exc=error, countdown=5, max_retries=2)
    assert len(recorder.retries) == 1
    assert recorder.failures == []
    assert dlq.transitions == []


def test_shutdown_timeout_does_not_change_task_dlq_behavior():
    incident = _incident()
    error = TerminalAgentFailure("invalid")
    task, recorder, dlq = _task(
        StubAgentExecutor(error=error),
        shutdown_timeout_seconds=120,
    )

    result = task.apply(args=(incident,), retries=0)

    assert result.state == "FAILURE"
    assert result.result is error
    assert recorder.retries == []
    assert len(recorder.failures) == 1
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].incident == incident
    assert dlq.transitions[0].error is error


def test_simulated_saturation_isolates_retry_and_dlq_state_per_event():
    retry_incident = _incident()
    terminal_incident = AcceptedIncidentFixture(
        event_id="event-2",
        sys_id="incident-2",
        number="INC0010002",
        event_type="Insert",
    )
    retry_error = RetryableAgentFailure("temporary")
    terminal_error = TerminalAgentFailure("invalid")
    retry_task, retry_recorder, retry_dlq = _task(
        StubAgentExecutor(error=retry_error)
    )
    terminal_task, terminal_recorder, terminal_dlq = _task(
        StubAgentExecutor(error=terminal_error)
    )

    with patch.object(retry_task, "retry", return_value="retry-scheduled"):
        retry_result = retry_task.apply(args=(retry_incident,), retries=0, throw=True)
    terminal_result = terminal_task.apply(args=(terminal_incident,), retries=0)

    assert retry_result.result == "retry-scheduled"
    assert len(retry_recorder.retries) == 1
    assert retry_recorder.retries[0].incident is retry_incident
    assert retry_recorder.failures == []
    assert retry_dlq.transitions == []

    assert terminal_result.state == "FAILURE"
    assert terminal_result.result is terminal_error
    assert terminal_recorder.retries == []
    assert len(terminal_recorder.failures) == 1
    assert terminal_recorder.failures[0].incident is terminal_incident
    assert len(terminal_dlq.transitions) == 1
    assert terminal_dlq.transitions[0].incident is terminal_incident

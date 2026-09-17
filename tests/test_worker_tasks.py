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


def _task(
    agent: StubAgentExecutor,
    *,
    max_retries: int = 2,
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
            worker_shutdown_timeout_seconds=60,
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

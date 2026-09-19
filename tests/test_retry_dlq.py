import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
from celery.contrib.testing.worker import start_worker
from celery.exceptions import TimeLimitExceeded

from src.config import WorkerConfig
from src.servicenow import exceptions as servicenow_exceptions
from src.workers.celery_app import create_celery_app
from src.workers.dlq import DeadLetterEntry
from src.workers.retry_policy import RetryDecision, RetryPolicy
from src.workers.runtime_integration import ExecutionContext, StateManagerTaskRecorder
from src.workers.tasks import (
    IntegrationSeams,
    register_process_accepted_incident_task,
)
from tests.worker_test_doubles import (
    RecordingDlq,
    RecordingStateRecorder,
    RetryableAgentFailure,
    StubAgentExecutor,
    TerminalAgentFailure,
    replay_dlq_payload,
)


class RetryableError(Exception):
    retryable = True


class TerminalError(Exception):
    retryable = False


CONFIRMED_PAYLOAD = {
    "sys_id": "a1b2c3d4e5f678901234567890abcdef",
    "event_id": "evt_9f8e7d6c5b4a3210",
    "event_type": "Insert",
    "number": "INC0012345",
}


def _local_worker_task(
    agent: object,
    policy: RetryPolicy,
    *,
    state_recorder: object | None = None,
    execution_context: object | None = None,
):
    """Create a local-only worker; its queue strings are not S2.1 contracts."""
    app = create_celery_app(
        WorkerConfig(
            broker_url="memory://",
            main_queue="test-main",
            dlq_queue="test-dlq",
            worker_concurrency=1,
            worker_prefetch_multiplier=1,
            task_soft_time_limit_seconds=10,
            task_time_limit_seconds=15,
            task_max_retries=policy.max_retries,
            retry_base_delay_seconds=policy.base_delay_seconds,
            retry_max_delay_seconds=policy.max_delay_seconds,
            task_acks_late=True,
            task_reject_on_worker_lost=True,
            worker_shutdown_timeout_seconds=10,
        )
    )
    recorder = state_recorder or RecordingStateRecorder()
    dlq = RecordingDlq()
    task = register_process_accepted_incident_task(
        policy,
        IntegrationSeams(
            agent=agent,  # type: ignore[arg-type]
            state_recorder=recorder,  # type: ignore[arg-type]
            dlq=dlq,
            execution_context=execution_context,
        ),
        app=app,
    )
    return app, task, recorder, dlq


@dataclass
class _FailTwiceThenSucceed:
    attempts_remaining: int = 2
    attempt_times: list[float] = field(default_factory=list)

    def execute(self, accepted_incident: object) -> object:
        del accepted_incident
        self.attempt_times.append(time.monotonic())
        if self.attempts_remaining:
            self.attempts_remaining -= 1
            raise RetryableAgentFailure("transient dependency failure")
        return "completed"


@dataclass
class _S2StateManager:
    """TEST-ONLY double of the published S2.2 facade used by S2.3."""

    create_execution_calls: list[tuple[object, ...]] = field(default_factory=list)
    retry_update_calls: list[tuple[object, ...]] = field(default_factory=list)
    failure_calls: list[tuple[object, ...]] = field(default_factory=list)
    statuses: list[tuple[str, str]] = field(default_factory=list)

    def create_execution(
        self,
        incident_reference: str,
        agent_version: str | None = None,
        model_name: str | None = None,
    ) -> object:
        self.create_execution_calls.append(
            (incident_reference, agent_version, model_name)
        )
        raise AssertionError("S2.3 retry/failure persistence must reuse one execution")

    def update_execution_status(
        self,
        execution_identifier: str,
        status: str,
    ) -> object:
        self.statuses.append((execution_identifier, status))
        return object()

    def update_retry_state(
        self,
        retry_state_id: int,
        attempt_count: int,
        last_error: str | None = None,
        next_attempt_time: object | None = None,
    ) -> object:
        self.retry_update_calls.append(
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
            (execution_reference, failing_node, error_class, message, retry_count)
        )
        return object()


def test_first_retry_uses_base_delay():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.delay_for_retry(1) == 5


def test_retry_delay_grows_exponentially():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert [policy.delay_for_retry(number) for number in (1, 2, 3)] == [5, 10, 20]


def test_retry_delay_is_capped():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=12,
        max_retries=3,
    )

    assert policy.delay_for_retry(3) == 12


def test_retryable_failure_retries_before_limit():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(RetryableError(), retries_completed=2) is RetryDecision.RETRY
    assert policy.decide(
        servicenow_exceptions.ServiceNowServerError(503, "unavailable"),
        retries_completed=0,
    ) is RetryDecision.RETRY


def test_retryable_failure_is_exhausted_at_limit():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(RetryableError(), retries_completed=3) is RetryDecision.EXHAUSTED


def test_retryable_failure_remains_exhausted_after_limit():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=2,
    )

    decisions = [
        policy.decide(RetryableError(), retries_completed=retries_completed)
        for retries_completed in range(4)
    ]

    assert decisions == [
        RetryDecision.RETRY,
        RetryDecision.RETRY,
        RetryDecision.EXHAUSTED,
        RetryDecision.EXHAUSTED,
    ]


def test_terminal_failure_does_not_retry():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(TerminalError(), retries_completed=0) is RetryDecision.TERMINAL
    assert policy.decide(TerminalError(), retries_completed=3) is RetryDecision.TERMINAL
    assert policy.decide(
        servicenow_exceptions.ServiceNowPermissionError(403, "forbidden"),
        retries_completed=0,
    ) is RetryDecision.TERMINAL


def test_delay_calculation_is_deterministic():
    policy = RetryPolicy(
        base_delay_seconds=7,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.delay_for_retry(3) == policy.delay_for_retry(3) == 28


def test_zero_retries_exhausts_retryable_failure_immediately():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=0,
    )

    assert policy.decide(RetryableError(), retries_completed=0) is RetryDecision.EXHAUSTED


def test_zero_delay_is_supported():
    policy = RetryPolicy(
        base_delay_seconds=0,
        max_delay_seconds=0,
        max_retries=3,
    )

    assert policy.delay_for_retry(1) == 0
    assert policy.delay_for_retry(3) == 0


@pytest.mark.parametrize(
    ("base_delay_seconds", "max_delay_seconds", "max_retries"),
    [
        (-1, 1, 1),
        (1, -1, 1),
        (1, 1, -1),
        (2, 1, 1),
    ],
)
def test_policy_rejects_invalid_configuration(
    base_delay_seconds,
    max_delay_seconds,
    max_retries,
):
    with pytest.raises(ValueError):
        RetryPolicy(
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
            max_retries=max_retries,
        )


@pytest.mark.parametrize("retry_number", [0, -1])
def test_delay_rejects_non_positive_retry_number(retry_number):
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    with pytest.raises(ValueError):
        policy.delay_for_retry(retry_number)


def test_empirical_celery_retry_intervals_follow_bounded_backoff():
    """Measure scheduled task retries with an in-memory Celery worker."""
    policy = RetryPolicy(base_delay_seconds=1, max_delay_seconds=2, max_retries=2)
    agent = _FailTwiceThenSucceed()
    app, task, recorder, dlq = _local_worker_task(agent, policy)

    with start_worker(
        app,
        pool="solo",
        concurrency=1,
        perform_ping_check=False,
        loglevel="WARNING",
    ):
        task.apply_async(args=(CONFIRMED_PAYLOAD,))
        deadline = time.monotonic() + 12
        while len(agent.attempt_times) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)

    assert len(agent.attempt_times) == 3

    intervals = [
        later - earlier
        for earlier, later in zip(agent.attempt_times, agent.attempt_times[1:])
    ]
    expected = [policy.delay_for_retry(1), policy.delay_for_retry(2)]
    print(f"measured Celery retry intervals: {intervals}")

    assert len(intervals) == 2
    for observed, configured in zip(intervals, expected):
        assert observed >= configured - 0.15
        assert observed <= configured + 2.0
    assert len(recorder.retries) == 2
    assert recorder.failures == []
    assert dlq.transitions == []


def test_malformed_and_hard_timeout_failures_go_directly_to_structured_dlq():
    malformed = TerminalAgentFailure("malformed accepted incident")
    execution_context = {"execution_reference": "s2-2-created-uuid"}
    _, malformed_task, malformed_recorder, malformed_dlq = _local_worker_task(
        StubAgentExecutor(error=malformed),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=2),
        execution_context=execution_context,
    )

    malformed_result = malformed_task.apply(args=(CONFIRMED_PAYLOAD,))

    assert malformed_result.state == "FAILURE"
    assert malformed_recorder.retries == []
    assert len(malformed_recorder.failures) == 1
    assert len(malformed_dlq.transitions) == 1
    malformed_entry = malformed_dlq.transitions[0]
    assert malformed_entry.payload is CONFIRMED_PAYLOAD
    assert malformed_entry.error_type == "TerminalAgentFailure"
    assert malformed_entry.error_message == "malformed accepted incident"
    assert malformed_entry.retry_count == 0
    assert malformed_entry.task_name
    assert malformed_entry.occurred_at.tzinfo is not None
    assert malformed_entry.execution_context is execution_context

    hard_timeout = TimeLimitExceeded()
    _, timeout_task, timeout_recorder, timeout_dlq = _local_worker_task(
        StubAgentExecutor(error=hard_timeout),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=2),
    )

    timeout_result = timeout_task.apply(args=(CONFIRMED_PAYLOAD,))

    assert timeout_result.state == "FAILURE"
    assert timeout_recorder.retries == []
    assert len(timeout_recorder.failures) == 1
    assert len(timeout_dlq.transitions) == 1
    assert timeout_dlq.transitions[0].error_type == "TimeLimitExceeded"


def test_worker_persists_retry_and_failure_through_injected_s2_2_adapter():
    state_manager = _S2StateManager()
    recorder = StateManagerTaskRecorder(
        ExecutionContext("s2-2-created-uuid", 42),
        "pending-worker-boundary",
        state_manager_factory=lambda: (state_manager, lambda: None),
    )
    retry_error = RetryableAgentFailure("temporary")
    _, retry_task, _, retry_dlq = _local_worker_task(
        StubAgentExecutor(error=retry_error),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
        state_recorder=recorder,
    )

    with patch.object(retry_task, "retry", return_value="scheduled") as retry:
        retry_result = retry_task.apply(args=(CONFIRMED_PAYLOAD,), retries=0, throw=True)

    assert retry_result.result == "scheduled"
    retry.assert_called_once_with(exc=retry_error, countdown=1, max_retries=1)
    assert state_manager.retry_update_calls == [
        (42, 1, "temporary", None)
    ]
    assert state_manager.create_execution_calls == []
    assert state_manager.failure_calls == []
    assert state_manager.statuses == []
    assert retry_dlq.transitions == []

    terminal_error = TerminalAgentFailure("invalid")
    _, terminal_task, _, terminal_dlq = _local_worker_task(
        StubAgentExecutor(error=terminal_error),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
        state_recorder=recorder,
    )

    terminal_result = terminal_task.apply(args=(CONFIRMED_PAYLOAD,))

    assert terminal_result.state == "FAILURE"
    assert state_manager.failure_calls == [
        (
            "s2-2-created-uuid",
            "pending-worker-boundary",
            "TerminalAgentFailure",
            "invalid",
            0,
        )
    ]
    assert state_manager.statuses == [("s2-2-created-uuid", "failed")]
    assert state_manager.create_execution_calls == []
    assert len(terminal_dlq.transitions) == 1


def test_dependency_fix_replays_exhausted_dlq_entry_once_without_new_failures():
    """Exercise local retry, DLQ preservation, dependency repair, and replay."""
    dependency_failure = RetryableAgentFailure("dependency unavailable")
    agent = StubAgentExecutor(error=dependency_failure)
    execution_context = {"execution_reference": "s2-2-created-uuid"}
    _, task, recorder, dlq = _local_worker_task(
        agent,
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
        execution_context=execution_context,
    )

    with patch.object(task, "retry", return_value="scheduled") as retry:
        retry_result = task.apply(args=(CONFIRMED_PAYLOAD,), retries=0, throw=True)

    assert retry_result.result == "scheduled"
    retry.assert_called_once_with(exc=dependency_failure, countdown=1, max_retries=1)
    assert len(recorder.retries) == 1
    assert recorder.failures == []
    assert dlq.transitions == []

    exhausted_result = task.apply(args=(CONFIRMED_PAYLOAD,), retries=1)

    assert exhausted_result.state == "FAILURE"
    assert len(recorder.failures) == 1
    assert len(dlq.transitions) == 1
    entry = dlq.transitions[0]
    assert entry.payload is CONFIRMED_PAYLOAD
    assert entry.execution_context is execution_context
    assert entry.error_type == "RetryableAgentFailure"
    assert entry.retry_count == 1

    agent.error = None
    agent.result = "dependency fixed"
    replay_invocations: list[object] = []

    def replay_processor(preserved_entry: DeadLetterEntry) -> object:
        replay_invocations.append(preserved_entry)
        return task.apply(
            args=(preserved_entry.payload,),
            retries=0,
            throw=True,
        ).result

    assert replay_dlq_payload(entry, replay_processor) == "dependency fixed"
    assert replay_invocations == [entry]
    assert agent.received_incidents == [CONFIRMED_PAYLOAD] * 3
    assert len(recorder.retries) == 1
    assert len(recorder.failures) == 1
    assert dlq.transitions == [entry]


def test_local_orchestration_simulation_isolates_poison_and_healthy_tasks():
    poison_error = TerminalAgentFailure("poison event")
    _, poison_task, poison_recorder, poison_dlq = _local_worker_task(
        StubAgentExecutor(error=poison_error),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
    )
    _, healthy_task, healthy_recorder, healthy_dlq = _local_worker_task(
        StubAgentExecutor(result="healthy"),
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        poison_future = executor.submit(poison_task.apply, args=(CONFIRMED_PAYLOAD,))
        healthy_future = executor.submit(healthy_task.apply, args=(CONFIRMED_PAYLOAD,))
        poison_result = poison_future.result(timeout=3)
        healthy_result = healthy_future.result(timeout=3)

    assert poison_result.state == "FAILURE"
    assert healthy_result.result == "healthy"
    assert poison_recorder.retries == []
    assert len(poison_recorder.failures) == 1
    assert len(poison_dlq.transitions) == 1
    assert healthy_recorder.retries == []
    assert healthy_recorder.failures == []
    assert healthy_dlq.transitions == []

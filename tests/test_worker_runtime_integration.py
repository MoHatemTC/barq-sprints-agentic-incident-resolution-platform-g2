from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app
from src.workers.dlq import create_dead_letter_entry, serialize_dead_letter_entry
from src.workers.redis_consumer import consume_next_incident_for_celery
from src.workers.retry_policy import RetryPolicy
from src.workers import runtime_integration
from src.workers.runtime_integration import (
    ExecutionContext,
    GraphAgentExecutor,
    StateManagerTaskRecorder,
    establish_execution_context,
)
from src.workers.tasks import ProductionIntegrationSeams, register_process_accepted_incident_task
from src.workers.worker_runtime import create_integrated_worker
from tests.worker_test_doubles import RecordingDlq


PAYLOAD = {
    "sys_id": "a1b2c3d4e5f678901234567890abcdef",
    "event_id": "evt_9f8e7d6c5b4a3210",
    "event_type": "Insert",
    "number": "INC0012345",
}


@dataclass
class _StateManager:
    create_execution_calls: list[object] = field(default_factory=list)
    create_retry_calls: list[object] = field(default_factory=list)
    retry_updates: list[object] = field(default_factory=list)
    failures: list[object] = field(default_factory=list)
    statuses: list[object] = field(default_factory=list)
    checkpoints: list[object] = field(default_factory=list)

    def create_execution(self, *, incident_reference, agent_version=None, model_name=None):
        self.create_execution_calls.append(incident_reference)
        return SimpleNamespace(
            execution_identifier="s2-2-execution-id",
            agent_version=agent_version,
            model_name=model_name,
        )

    def create_retry_state(self, *, execution_reference, attempt_count):
        self.create_retry_calls.append((execution_reference, attempt_count))
        return SimpleNamespace(id=17)

    def update_retry_state(self, **kwargs):
        self.retry_updates.append(kwargs)

    def record_failure(self, **kwargs):
        self.failures.append(kwargs)

    def update_execution_status(self, execution_identifier, status):
        self.statuses.append((execution_identifier, status))

    def save_checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)


def test_establish_execution_context_uses_s2_2_generated_identifiers_only():
    manager = _StateManager()
    closed = []

    context = establish_execution_context(
        PAYLOAD,
        state_manager_factory=lambda: (manager, lambda: closed.append(True)),
    )

    assert context == ExecutionContext("s2-2-execution-id", 17)
    assert manager.create_execution_calls == ["INC0012345"]
    assert manager.create_retry_calls == [("s2-2-execution-id", 0)]
    assert closed == [True]
    assert PAYLOAD["event_id"] not in context.execution_identifier
    assert PAYLOAD["sys_id"] not in context.execution_identifier


def test_s2_2_recorder_reuses_context_for_retry_failure_and_success():
    manager = _StateManager()
    recorder = StateManagerTaskRecorder(
        ExecutionContext("s2-2-execution-id", 17),
        "celery_worker",
        state_manager_factory=lambda: (manager, lambda: None),
    )

    error = RuntimeError("temporary")
    recorder.record_retry(PAYLOAD, 0, error)
    recorder.record_failure(PAYLOAD, 1, error)
    with patch.object(runtime_integration, "_sync_servicenow_completion") as sync:
        recorder.record_success(
            PAYLOAD,
            {
                "classification": "software",
                "risk": "low",
                "action_taken": "interrupted:no_evidence",
                "incident_payload": {"large": "raw servicenow payload"},
            },
        )

    assert manager.retry_updates == [
        {
            "retry_state_id": 17,
            "attempt_count": 1,
            "last_error": "temporary",
            "next_attempt_time": None,
        }
    ]
    assert manager.failures[0]["execution_reference"] == "s2-2-execution-id"
    assert manager.failures[0]["retry_count"] == 1
    assert manager.statuses == [
        ("s2-2-execution-id", "failed"),
        ("s2-2-execution-id", "succeeded"),
    ]
    assert manager.checkpoints == [
        {
            "execution_reference": "s2-2-execution-id",
            "node_name": "interrupted:no_evidence",
            "checkpoint": (
                '{"classification": "software", "risk": "low", '
                '"action_taken": "interrupted:no_evidence"}'
            ),
        }
    ]
    sync.assert_called_once()


def test_servicenow_completion_fields_contain_actionable_graph_result():
    fields = runtime_integration._servicenow_completion_fields(
        {
            "classification": "access",
            "risk": "low",
            "confidence": 0.85,
            "action_taken": "resolved_automatically",
            "critic_verdict": {"passed": True},
            "outputs": {
                "diagnosis": "A cached credential was used.",
                "resolution": "1. Clear the cached credential. [Source: KB0001]",
            },
        },
        {
            "processing_start": "2026-09-24 10:00:00",
            "processing_end": "2026-09-24 10:01:00",
            "retry_count": 1,
            "max_retries": 3,
            "agent_version": "sprint-3.1",
            "model_name": "gemini-3.6-flash",
        },
    )

    assert fields == {
        "processing_state": "complete",
        "processing_start": "2026-09-24 10:00:00",
        "processing_end": "2026-09-24 10:01:00",
        "max_retries": 3,
        "retry_count": 1,
        "retry_time_out": None,
        "agent_version": "sprint-3.1",
        "model_name": "gemini-3.6-flash",
        "classification": "access",
        "confidence": 0.85,
        "suggestion": "A cached credential was used.",
        "resolution": "1. Clear the cached credential. [Source: KB0001]",
        "human_review": False,
        "failure_reason": None,
    }


def test_graph_executor_uses_s2_5_graph_inputs_and_execution_id():
    graph_calls = []

    class _Graph:
        def invoke(self, state, config):
            graph_calls.append((state, config))
            return {"action_taken": "resolved_automatically"}

    graph_module = SimpleNamespace(compile_graph=lambda checkpointer: _Graph())
    checkpointer_module = SimpleNamespace(get_checkpointer=lambda: "checkpoint")
    tracing_module = SimpleNamespace(trace_execution=lambda name: lambda function: function)

    with patch(
        "src.workers.runtime_integration.import_module",
        side_effect=[graph_module, checkpointer_module, tracing_module],
    ):
        result = GraphAgentExecutor().execute(
            PAYLOAD,
            execution_id="s2-2-execution-id",
            incident_number="INC0012345",
        )

    assert result == {"action_taken": "resolved_automatically"}
    assert graph_calls == [
        (
            {
                "execution_id": "s2-2-execution-id",
                "incident_number": "INC0012345",
                "incident_payload": PAYLOAD,
            },
            {"configurable": {"thread_id": "s2-2-execution-id"}},
        )
    ]


def test_redis_celery_dispatch_carries_internal_context_in_headers():
    class _Redis:
        def blpop(self, keys, timeout=0):
            return (keys, b'{"sys_id":"a","event_id":"b","event_type":"Insert","number":"INC1"}')

    class _Task:
        calls = []

        def apply(self, args):
            raise AssertionError("production dispatch must not apply synchronously")

        def apply_async(self, args, headers=None):
            self.calls.append((args, headers))

    task = _Task()
    context = ExecutionContext("s2-2-execution-id", 17)

    assert consume_next_incident_for_celery(
        _Redis(), task, lambda payload: context, block_timeout_seconds=2
    ) is True

    assert task.calls == [( ( {
        "sys_id": "a", "event_id": "b", "event_type": "Insert", "number": "INC1"
    },), context.task_headers())]


def test_production_task_uses_header_context_and_records_success(monkeypatch):
    app = create_celery_app(
        WorkerConfig(
            broker_url="memory://",
            main_queue="test-main",
            dlq_queue="test-dlq",
            worker_concurrency=1,
            worker_prefetch_multiplier=1,
            task_soft_time_limit_seconds=10,
            task_time_limit_seconds=15,
            task_max_retries=1,
            retry_base_delay_seconds=1,
            retry_max_delay_seconds=2,
            task_acks_late=True,
            task_reject_on_worker_lost=True,
            worker_shutdown_timeout_seconds=10,
        )
    )
    calls = []

    class _Agent:
        def execute(self, incident, execution_id=None, incident_number=None):
            calls.append((incident, execution_id, incident_number))
            return {"status": "ok"}

    class _Recorder:
        def __init__(self, context, failing_node):
            self.context = context

        def record_retry(self, *args):
            raise AssertionError("success must not retry")

        def record_failure(self, *args):
            raise AssertionError("success must not fail")

        def record_success(self, incident, result):
            calls.append(("success", self.context.execution_identifier, result))

    monkeypatch.setattr("src.workers.tasks.GraphAgentExecutor", _Agent)
    monkeypatch.setattr("src.workers.tasks.StateManagerTaskRecorder", _Recorder)
    task = register_process_accepted_incident_task(
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=2, max_retries=1),
        ProductionIntegrationSeams(dlq=RecordingDlq()),
        app=app,
    )

    result = task.apply(
        args=(PAYLOAD,),
        headers=ExecutionContext("s2-2-execution-id", 17).task_headers(),
        throw=True,
    )

    assert result.result == {"status": "ok"}
    assert calls == [
        (PAYLOAD, "s2-2-execution-id", "INC0012345"),
        ("success", "s2-2-execution-id", {"status": "ok"}),
    ]


def test_production_task_retries_with_the_same_header_context(monkeypatch):
    app = create_celery_app(
        WorkerConfig(
            broker_url="memory://", main_queue="test-main", dlq_queue="test-dlq",
            worker_concurrency=1, worker_prefetch_multiplier=1,
            task_soft_time_limit_seconds=10, task_time_limit_seconds=15,
            task_max_retries=1, retry_base_delay_seconds=1, retry_max_delay_seconds=2,
            task_acks_late=True, task_reject_on_worker_lost=True,
            worker_shutdown_timeout_seconds=10,
        )
    )
    context = ExecutionContext("s2-2-execution-id", 17)
    retry_calls = []

    class _RetryableError(Exception):
        retryable = True

    class _Agent:
        def execute(self, *args, **kwargs):
            raise _RetryableError("temporary")

    class _Recorder:
        def __init__(self, received_context, failing_node):
            assert received_context == context

        def record_retry(self, incident, retries_completed, error):
            retry_calls.append((incident, retries_completed, str(error)))

        def record_failure(self, *args):
            raise AssertionError("retryable first failure must not be terminal")

    monkeypatch.setattr("src.workers.tasks.GraphAgentExecutor", _Agent)
    monkeypatch.setattr("src.workers.tasks.StateManagerTaskRecorder", _Recorder)
    task = register_process_accepted_incident_task(
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=2, max_retries=1),
        ProductionIntegrationSeams(dlq=RecordingDlq()), app=app,
    )

    with patch.object(task, "retry", return_value="scheduled") as retry:
        result = task.apply(
            args=(PAYLOAD,), headers=context.task_headers(), retries=0, throw=True
        )

    assert result.result == "scheduled"
    assert retry_calls == [(PAYLOAD, 0, "temporary")]
    retry.assert_called_once()
    assert type(retry.call_args.kwargs["exc"]).__name__ == "_RetryableError"
    assert retry.call_args.kwargs["countdown"] == 1
    assert retry.call_args.kwargs["max_retries"] == 1
    assert retry.call_args.kwargs["headers"] == context.task_headers()


def test_redis_dlq_serialization_preserves_payload_and_execution_context():
    entry = create_dead_letter_entry(
        payload=PAYLOAD,
        execution_context=ExecutionContext("s2-2-execution-id", 17),
        error=RuntimeError("failed"),
        retry_count=1,
        task_name="worker.task",
        task_id="task-id",
    )

    serialized = serialize_dead_letter_entry(entry)

    assert '"payload":{"sys_id":"a1b2c3d4e5f678901234567890abcdef"' in serialized
    assert '"execution_identifier":"s2-2-execution-id"' in serialized
    assert '"retry_state_id":17' in serialized
    assert '"error_type":"RuntimeError"' in serialized


def test_integrated_worker_uses_one_s2_3_celery_app_and_configured_dlq():
    class _Redis:
        def blpop(self, keys, timeout=0):
            return None

        def rpush(self, key, value):
            raise AssertionError("no DLQ transition on worker construction")

    config = WorkerConfig(
        broker_url="memory://", main_queue="unused-by-s2-1-list", dlq_queue="dlq-list",
        worker_concurrency=2, worker_prefetch_multiplier=1,
        task_soft_time_limit_seconds=10, task_time_limit_seconds=15,
        task_max_retries=1, retry_base_delay_seconds=1, retry_max_delay_seconds=2,
        task_acks_late=True, task_reject_on_worker_lost=True,
        worker_shutdown_timeout_seconds=10,
    )

    worker = create_integrated_worker(_Redis(), config)

    assert worker.process_task.app.conf.worker_concurrency == 2
    assert worker.consume_once(block_timeout_seconds=1) is False

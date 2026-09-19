import json
from dataclasses import dataclass, field

import pytest

from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app
from src.workers.redis_consumer import (
    INCIDENT_EVENTS_LIST,
    consume_next_incident,
    deserialize_incident_message,
)
from src.workers.retry_policy import RetryPolicy
from src.workers.tasks import (
    IntegrationSeams,
    register_process_accepted_incident_task,
)
from tests.worker_test_doubles import (
    RecordingDlq,
    RecordingStateRecorder,
    StubAgentExecutor,
)


S2_1_PAYLOAD = {
    "sys_id": "a1b2c3d4e5f678901234567890abcdef",
    "event_id": "evt_9f8e7d6c5b4a3210",
    "event_type": "Insert",
    "number": "INC0012345",
}


@dataclass
class _FakeRedisList:
    values: list[object] = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)

    def blpop(self, keys: str, timeout: int = 0) -> object | None:
        self.calls.append((keys, timeout))
        if not self.values:
            return None
        return (keys, self.values.pop(0))


def _task(agent: StubAgentExecutor):
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
    recorder = RecordingStateRecorder()
    dlq = RecordingDlq()
    task = register_process_accepted_incident_task(
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=2, max_retries=1),
        IntegrationSeams(
            agent=agent,
            state_recorder=recorder,
            dlq=dlq,
        ),
        app=app,
    )
    return task, recorder, dlq


def test_redis_consumer_passes_s2_1_json_to_existing_task_unchanged():
    payload_json = json.dumps(S2_1_PAYLOAD)
    redis_client = _FakeRedisList(values=[payload_json.encode("utf-8")])
    agent = StubAgentExecutor(result="processed")
    task, recorder, dlq = _task(agent)

    assert consume_next_incident(redis_client, task, block_timeout_seconds=7) is True

    assert redis_client.calls == [(INCIDENT_EVENTS_LIST, 7)]
    assert agent.received_incidents == [S2_1_PAYLOAD]
    assert agent.received_incidents[0] == S2_1_PAYLOAD
    assert recorder.retries == []
    assert recorder.failures == []
    assert dlq.transitions == []


def test_redis_consumer_accepts_exact_four_field_s2_1_payload():
    decoded = deserialize_incident_message(json.dumps(S2_1_PAYLOAD))

    assert decoded == S2_1_PAYLOAD
    assert set(decoded) == {"sys_id", "event_id", "event_type", "number"}


@pytest.mark.parametrize(
    "raw_message",
    ["{not-json", json.dumps({"event_id": "event-only"})],
)
def test_malformed_list_messages_follow_terminal_dlq_path(raw_message):
    redis_client = _FakeRedisList(values=[raw_message])
    agent = StubAgentExecutor(result="must not run")
    task, recorder, dlq = _task(agent)

    assert consume_next_incident(redis_client, task) is True

    assert agent.received_incidents == []
    assert recorder.retries == []
    assert len(recorder.failures) == 1
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].payload == raw_message
    assert dlq.transitions[0].execution_context is None
    assert dlq.transitions[0].error_type == "ValueError"


def test_consumer_continues_after_malformed_message_without_inventing_context():
    malformed_json = "{not-json"
    redis_client = _FakeRedisList(
        values=[malformed_json, json.dumps(S2_1_PAYLOAD)]
    )
    agent = StubAgentExecutor(result="healthy")
    task, recorder, dlq = _task(agent)

    assert consume_next_incident(redis_client, task) is True
    assert consume_next_incident(redis_client, task) is True

    assert agent.received_incidents == [S2_1_PAYLOAD]
    assert len(recorder.failures) == 1
    assert len(dlq.transitions) == 1
    assert dlq.transitions[0].payload == malformed_json
    assert dlq.transitions[0].execution_context is None


def test_consumer_uses_blocking_read_and_returns_on_empty_list():
    redis_client = _FakeRedisList()
    task, _, _ = _task(StubAgentExecutor(result="unused"))

    assert consume_next_incident(redis_client, task, block_timeout_seconds=3) is False
    assert redis_client.calls == [(INCIDENT_EVENTS_LIST, 3)]

from src.workers.redis_consumer import run_incident_consumer_for_celery
def test_consumer_loop_graceful_shutdown_stops_accepting_new_work():
    redis_client = _FakeRedisList(values=[json.dumps(S2_1_PAYLOAD).encode('utf-8'), json.dumps(S2_1_PAYLOAD).encode('utf-8')])
    stop_flags = [False, True]
    def should_stop(): return stop_flags.pop(0) if stop_flags else True
    class FakeContext:
        def task_headers(self): return {'exec_id': '123'}
    class FakeTaskInvoker:
        def __init__(self): self.calls = []
        def apply_async(self, args, headers=None): self.calls.append((args, headers))
    invoker = FakeTaskInvoker()
    run_incident_consumer_for_celery(redis_client, invoker, lambda p: FakeContext(), should_stop, block_timeout_seconds=1)
    assert len(invoker.calls) == 1
    assert invoker.calls[0][0][0] == S2_1_PAYLOAD
    assert len(redis_client.values) == 1

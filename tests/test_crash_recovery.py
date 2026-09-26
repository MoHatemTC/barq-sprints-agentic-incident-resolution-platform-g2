"""test for S3.4 if a worker killed mid-execution recovers from its last checkpoint without duplicating the ServiceNow write"""

from unittest.mock import patch, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.agent.graph import create_graph
from src.agent.nodes.act import ExecutionLogNotWritten
from tests.agent_registry_helpers import (
    FakeServiceNowClient,
    build_registry,
    install_registry,
)

pytestmark = pytest.mark.usefixtures("hermetic_llm")


SYS_ID = "sys-0001"
HIGH_RISK = {"sys_id": SYS_ID, "description": "this is a high-risk task"}
NORMAL = {"sys_id": SYS_ID, "description": "normal issue"}


class WorkerKilled(Exception):
    """Stands in for the worker process dying at a chosen point."""

class FakeServiceNow:
    """Records writes; kills the 'worker' once at kill_at."""

    def __init__(self, kill_at=None):
        self.kill_at = kill_at
        self.patches = []
        self.logs = []

    def _maybe_kill(self, point):
        if self.kill_at == point:
            self.kill_at = None  # only the first attempt dies
            raise WorkerKilled(point)

    def find_execution_log(self, execution_id, action):
        return next(
            (r for r in self.logs if r["execution_id"] == execution_id and r["action"] == action),
            None,
        )

    def update_incident(self, sys_id, fields):
        self._maybe_kill("before_patch")
        self.patches.append({"sys_id": sys_id, **fields})
        self._maybe_kill("after_patch")
        return fields

    def write_execution_log(self, incident_sys_id, execution_id, action, status,
                            agent=None, result=None, error=None):
        row = {"incident": incident_sys_id, "execution_id": execution_id,
               "action": action, "status": status, "result": result}
        self.logs.append(row)
        self._maybe_kill("after_log")
        return row


def _chunk():
    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "p1"
    chunk.text = "Reset the VPN credentials"
    chunk.score = 5.0
    return chunk


@pytest.fixture(autouse=True)
def _no_real_servicenow(monkeypatch):
    # load_node reads the incident when a sys_id is present, through the
    # registry rather than a directly constructed client.
    install_registry(monkeypatch, "src.agent.nodes.load", build_registry())
    with patch("src.agent.nodes.retrieve.search", return_value=[_chunk()]):
        yield


@pytest.fixture
def fake(monkeypatch):
    client = FakeServiceNow()
    # The S3.4 double already speaks the client's Table API surface, so it can
    # sit behind a real IncidentGateway unchanged.
    install_registry(monkeypatch, "src.agent.nodes.act", build_registry(client))
    return client


def _graph():
    return create_graph().compile(checkpointer=MemorySaver())


def _cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _initial(thread_id, incident):
    return {"execution_id": thread_id, "incident_number": "INC0001", "incident_payload": incident}


# normal writes tests

def test_automatic_run_writes_once(fake):
    result = _graph().invoke(_initial("auto", NORMAL), config=_cfg("auto"))

    assert result["servicenow_write"] == "written"
    assert len(fake.logs) == 1
    assert fake.logs[0]["action"] == "auto_resolve"
    assert fake.logs[0]["status"] == "succeeded"
    assert fake.patches[0]["processing_state"] == "complete"


def test_no_write_while_paused(fake):
    graph = _graph()
    result = graph.invoke(_initial("paused", HIGH_RISK), config=_cfg("paused"))

    assert "__interrupt__" in result
    assert fake.patches == []
    assert fake.logs == []


def test_approve_writes_resolution(fake):
    graph = _graph()
    graph.invoke(_initial("approve", HIGH_RISK), config=_cfg("approve"))
    result = graph.invoke(
        Command(resume={"decision": "approve", "reviewer": "alice"}), config=_cfg("approve")
    )

    assert result["servicenow_write"] == "written"
    assert [r["action"] for r in fake.logs] == ["approved_resolve"]
    assert "alice" in fake.logs[0]["result"]
    assert fake.patches[0]["processing_state"] == "complete"


def test_reject_writes_escalation(fake):
    graph = _graph()
    graph.invoke(_initial("reject", HIGH_RISK), config=_cfg("reject"))
    result = graph.invoke(
        Command(resume={"decision": "reject", "reviewer": "bob", "comment": "wrong KB"}),
        config=_cfg("reject"),
    )

    assert result["action_taken"] == "rejected_by_human"
    assert [(r["action"], r["status"]) for r in fake.logs] == [("escalated_rejected", "blocked")]
    assert fake.patches[0]["human_review"] is True
    assert fake.patches[0]["failure_reason"] == "Rejected by bob: wrong KB"
    assert "processing_state" not in fake.patches[0]


def test_missing_sys_id_skips_write(fake):
    result = _graph().invoke(
        _initial("nosys", {"description": "normal issue"}), config=_cfg("nosys")
    )

    assert result["servicenow_write"] == "skipped_no_sys_id"
    assert fake.logs == []


# crash recovery tests

@pytest.mark.parametrize(
    "kill_at, patches_after_retry, retry_outcome",
    [
        ("before_patch", 1, "written"),       # killed before the write
        ("after_patch", 2, "written"),        # PATCH repeated, harmless
        ("after_log", 1, "already_done"),     # killed after the write: receipt found
    ],
)
def test_automatic_run_recovers_without_duplicate(fake, kill_at, patches_after_retry, retry_outcome):
    graph = _graph()
    fake.kill_at = kill_at

    with pytest.raises(WorkerKilled):
        graph.invoke(_initial("crash-" + kill_at, NORMAL), config=_cfg("crash-" + kill_at))

    # the checkpoint stops before act, so the retry resumes there
    assert graph.get_state(_cfg("crash-" + kill_at)).next == ("act",)

    result = graph.invoke(None, config=_cfg("crash-" + kill_at))

    assert result["servicenow_write"] == retry_outcome
    assert len(fake.logs) == 1
    assert len(fake.patches) == patches_after_retry


@pytest.mark.parametrize("kill_at", ["before_patch", "after_log"])
def test_approved_run_recovers_without_duplicate(fake, kill_at):
    """Kill during the resumed run: the retry still uses the human decision."""
    graph = _graph()
    cfg = _cfg("approve-crash-" + kill_at)
    graph.invoke(_initial("approve-crash-" + kill_at, HIGH_RISK), config=cfg)

    fake.kill_at = kill_at
    with pytest.raises(WorkerKilled):
        graph.invoke(Command(resume={"decision": "approve", "reviewer": "alice"}), config=cfg)

    result = graph.invoke(None, config=cfg)

    assert result["action_taken"] == "approved_by_human"
    assert [r["action"] for r in fake.logs] == ["approved_resolve"]


def test_failed_receipt_is_retried(fake):
    """write_execution_log swallows errors; act must raise so the task retries."""
    graph = _graph()
    real_write = fake.write_execution_log
    fake.write_execution_log = lambda *a, **k: None

    with pytest.raises(ExecutionLogNotWritten):
        graph.invoke(_initial("receipt", NORMAL), config=_cfg("receipt"))

    fake.write_execution_log = real_write
    result = graph.invoke(None, config=_cfg("receipt"))

    assert result["servicenow_write"] == "written"
    assert len(fake.logs) == 1


#  the worker executor recovers from the checkpoint tests

from src.agent.nodes import act as act_module
from src.config import WorkerConfig
from src.observability.tracing import _is_graph_pause
from src.workers import tasks


class AuditLog:
    def __init__(self):
        self.rows = []

    def __call__(self, execution_id, node_name, data):
        self.rows.append((execution_id, node_name, data))

    def names(self):
        return [name for _, name, _ in self.rows]


@pytest.fixture
def executor(monkeypatch):
    """The real worker executor on an in-memory checkpointer."""
    graph = _graph()
    monkeypatch.setattr(tasks, "compile_graph", lambda checkpointer=None: graph)
    monkeypatch.setattr(tasks, "get_checkpointer", lambda: None)
    audit = AuditLog()
    return tasks.GraphAgentExecutor(audit=audit), graph, audit


def _execute(agent, eid, incident):
    return agent.execute(incident, execution_id=eid, incident_number="INC0001")


def test_redelivered_task_continues_from_checkpoint(executor, fake, monkeypatch):
    agent, graph, audit = executor
    fake.kill_at = "before_patch"

    with pytest.raises(WorkerKilled):
        _execute(agent, "redeliver", NORMAL)

    # did not start again from load: give load its own instrumented registry and
    # assert the resume never dispatched a read_incident.
    load_spy_client = FakeServiceNowClient()
    install_registry(
        monkeypatch, "src.agent.nodes.load", build_registry(load_spy_client)
    )
    result = _execute(agent, "redeliver", NORMAL)  # Celery re-delivers the same task
    assert load_spy_client.calls == []

    assert result["servicenow_write"] == "written"
    assert len(fake.logs) == 1
    assert audit.names() == [tasks.AUDIT_RESUME_CRASH, tasks.AUDIT_RESULT]
    assert audit.rows[0][2]["from_node"] == ["act"]
    assert audit.rows[1][2]["action_taken"] == "resolved_automatically"


def test_killed_after_write_recovers_without_second_write(executor, fake):
    agent, graph, audit = executor
    fake.kill_at = "after_log"

    with pytest.raises(WorkerKilled):
        _execute(agent, "after", NORMAL)
    result = _execute(agent, "after", NORMAL)

    assert result["servicenow_write"] == "already_done"
    assert len(fake.logs) == 1
    assert audit.names() == [tasks.AUDIT_RESUME_CRASH, tasks.AUDIT_RESULT]


def test_pause_is_audited_once_even_if_redelivered(executor, fake):
    agent, graph, audit = executor

    first = _execute(agent, "pause", HIGH_RISK)
    again = _execute(agent, "pause", HIGH_RISK)

    assert "__interrupt__" in first and "__interrupt__" in again
    assert audit.names() == [tasks.AUDIT_INTERRUPT]
    assert audit.rows[0][2]["payload"]["gate"] == "high_risk"
    assert fake.logs == []


def test_human_resume_and_crash_recovery_are_audited_differently(executor, fake):
    agent, graph, audit = executor
    _execute(agent, "both", HIGH_RISK)
    fake.kill_at = "before_patch"

    with pytest.raises(WorkerKilled):
        agent.resume(execution_id="both", decision={"decision": "approve", "reviewer": "alice"})
    result = agent.resume(execution_id="both", decision={"decision": "approve", "reviewer": "alice"})

    assert result["action_taken"] == "approved_by_human"
    assert len(fake.logs) == 1
    assert audit.names() == [
        tasks.AUDIT_INTERRUPT, tasks.AUDIT_RESUME_HUMAN, tasks.AUDIT_RESUME_CRASH, tasks.AUDIT_RESULT,
    ]
    assert audit.rows[1][2]["reviewer"] == "alice"
    # the crash recovery kept the human decision it resumed with
    assert audit.rows[2][2]["human_decision"]["decision"] == "approve"


def test_finished_run_redelivered_does_nothing(executor, fake):
    agent, graph, audit = executor
    _execute(agent, "done", NORMAL)

    result = _execute(agent, "done", NORMAL)

    assert result["servicenow_write"] == "written"
    assert len(fake.logs) == 1
    assert audit.names() == [tasks.AUDIT_RESULT]  # only the first run's outcome


# demo crash switch tests
class ProcessExit(BaseException):
    pass


@pytest.fixture
def exits(monkeypatch):
    calls = []

    def fake_exit(code):
        calls.append(code)
        raise ProcessExit(code)

    markers = set()

    def first(execution_id, point):
        key = (execution_id, point)
        if key in markers:
            return False
        markers.add(key)
        return True

    monkeypatch.setattr(act_module.os, "_exit", fake_exit)
    monkeypatch.setattr(act_module, "_first_demo_crash", first)
    return calls


def test_demo_crash_is_off_by_default(monkeypatch, exits):
    monkeypatch.delenv("DEMO_CRASH_AT", raising=False)
    act_module.demo_crash("before_write", "e1")
    assert exits == []


def test_demo_crash_fires_once_at_its_point(monkeypatch, exits):
    monkeypatch.setenv("DEMO_CRASH_AT", "after_write")

    act_module.demo_crash("before_write", "e1")          # other point: nothing
    with pytest.raises(ProcessExit):
        act_module.demo_crash("after_write", "e1")       # dies
    act_module.demo_crash("after_write", "e1")           # recovered attempt: survives

    assert exits == [137]


def test_demo_crash_without_redis_never_kills(monkeypatch):
    monkeypatch.setenv("DEMO_CRASH_AT", "before_write")
    monkeypatch.delenv("REDIS_URL", raising=False)
    act_module.demo_crash("before_write", "e1")  # no marker store: stays alive


#  tracing and celery config tests

def test_graph_pause_is_not_traced_as_error():
    from langgraph.errors import GraphInterrupt
    assert _is_graph_pause(GraphInterrupt())
    assert not _is_graph_pause(ValueError("boom"))


def _worker_env(**extra):
    env = {
        "CELERY_BROKER_URL": "redis://x:6379/0", "CELERY_MAIN_QUEUE": "m", "CELERY_DLQ_QUEUE": "d",
        "CELERY_WORKER_CONCURRENCY": "1", "CELERY_WORKER_PREFETCH_MULTIPLIER": "1",
        "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": "30", "CELERY_TASK_TIME_LIMIT_SECONDS": "60",
        "CELERY_TASK_MAX_RETRIES": "3", "CELERY_RETRY_BASE_DELAY_SECONDS": "5",
        "CELERY_RETRY_MAX_DELAY_SECONDS": "60", "CELERY_TASK_ACKS_LATE": "true",
        "CELERY_TASK_REJECT_ON_WORKER_LOST": "true", "CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS": "10",
    }
    env.update(extra)
    return env


def test_visibility_timeout_default_and_override():
    from src.workers.celery_app import create_celery_app

    assert WorkerConfig.from_environment(_worker_env()).visibility_timeout_seconds == 120
    config = WorkerConfig.from_environment(_worker_env(CELERY_VISIBILITY_TIMEOUT_SECONDS="45"))
    assert create_celery_app(config).conf.broker_transport_options == {"visibility_timeout": 45}

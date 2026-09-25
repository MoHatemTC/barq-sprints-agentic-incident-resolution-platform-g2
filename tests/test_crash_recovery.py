"""test for S3.4 if a worker killed mid-execution recovers from its last checkpoint without duplicating the ServiceNow write"""

from unittest.mock import patch, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.agent.graph import create_graph
from src.agent.nodes.act import ExecutionLogNotWritten


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
def _no_real_servicenow():
    # load_node reads the incident when a sys_id is present
    with patch("src.agent.nodes.load.ServiceNowClient") as load_client, \
         patch("src.agent.nodes.retrieve.search", return_value=[_chunk()]):
        load_client.return_value.get_incident.return_value = {}
        yield


@pytest.fixture
def fake(monkeypatch):
    client = FakeServiceNow()
    monkeypatch.setattr("src.agent.nodes.act.get_servicenow_client", lambda: client)
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

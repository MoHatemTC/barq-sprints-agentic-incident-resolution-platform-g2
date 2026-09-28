"""ServiceNow's AI Processing State follows the run: In Progress while the agent
works, Failed when it dead-letters or a resume after approval fails.

Written through ToolRegistry like every other agent write, and best effort:
a ServiceNow problem must never stop the run or the dead-letter record.
"""

import pytest
from celery import Celery

from src.agent.tools.registry import ToolRefusal
from src.workers import runtime_integration, tasks
from src.workers.runtime_integration import ExecutionContext, StateManagerTaskRecorder

PAYLOAD = {"sys_id": "abc123", "number": "INC0010300"}


class FakeRegistry:
    def __init__(self, fail=(), refuse=(), events=None):
        self.calls, self.fail, self.refuse, self.events = [], set(fail), set(refuse), events

    def dispatch(self, tool, execution_id, **kwargs):
        self.calls.append((tool, execution_id, kwargs))
        if self.events is not None:
            self.events.append((tool, (kwargs.get("fields") or {}).get("processing_state")))
        if tool in self.fail:
            raise ConnectionError("ServiceNow unreachable")
        if tool in self.refuse:
            return ToolRefusal(tool=tool, execution_id=execution_id, reason="unregistered", message="no")
        return {"status": "ok"}


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr("src.agent.tools.registry.DEFAULT_TOOL_REGISTRY", fake)
    return fake


def test_failure_marks_the_incident_failed_with_a_work_note(registry):
    runtime_integration._sync_servicenow_failure(PAYLOAD, "exec-1", 3, TimeoutError("LLM timed out"))

    (fields_tool, eid, fields_args), (note_tool, _, note_args) = registry.calls
    assert (fields_tool, note_tool, eid) == ("write_ai_fields", "write_work_note", "exec-1")
    assert fields_args["sys_id"] == note_args["sys_id"] == "abc123"
    fields = fields_args["fields"]
    assert fields["processing_state"] == "failed"  # a state the Business Rule never re-sends
    assert fields["failure_reason"] == "TimeoutError: LLM timed out"
    assert fields["retry_count"] == 3 and fields["processing_end"]
    note = note_args["note"]
    assert "after 3 retries" in note and "dead-letter queue" in note
    assert "TimeoutError: LLM timed out" in note and "exec-1" in note
    assert "set AI Processing State back to Pending" in note  # how to retry


def test_servicenow_outage_is_logged_not_raised(monkeypatch, caplog):
    fake = FakeRegistry(fail={"write_ai_fields"}, refuse={"write_work_note"})
    monkeypatch.setattr("src.agent.tools.registry.DEFAULT_TOOL_REGISTRY", fake)

    runtime_integration._sync_servicenow_failure(PAYLOAD, "exec-2", 0, ValueError("bad"))

    assert [c[0] for c in fake.calls] == ["write_ai_fields", "write_work_note"]  # still tries the note
    assert "could not reach ServiceNow" in caplog.text
    assert "refused write_work_note" in caplog.text


def test_payload_without_sys_id_writes_nothing(registry):
    runtime_integration._sync_servicenow_failure({"number": "INC1"}, "exec-3", 0, ValueError("x"))
    runtime_integration._sync_servicenow_failure("not a mapping", "exec-3", 0, ValueError("x"))
    assert registry.calls == []


class _StateManager:
    def __init__(self):
        self.failures, self.statuses = [], []

    def record_failure(self, **kwargs):
        self.failures.append(kwargs)

    def update_execution_status(self, execution_id, status):
        self.statuses.append((execution_id, status))


def test_recorder_persists_the_failure_then_marks_servicenow(monkeypatch):
    fake = FakeRegistry(fail={"write_ai_fields", "write_work_note"})
    monkeypatch.setattr("src.agent.tools.registry.DEFAULT_TOOL_REGISTRY", fake)
    manager = _StateManager()
    recorder = StateManagerTaskRecorder(
        ExecutionContext("exec-4", 7), "celery_worker",
        state_manager_factory=lambda: (manager, lambda: None),
    )

    recorder.record_failure(PAYLOAD, 2, RuntimeError("boom"))  # does not raise: the DLQ step still runs

    assert manager.statuses == [("exec-4", "failed")]
    assert [c[0] for c in fake.calls] == ["write_ai_fields", "write_work_note"]


# In Progress while the agent works

def _states(registry):
    return [(tool, (kw.get("fields") or {}).get("processing_state")) for tool, _, kw in registry.calls]


class _FullStateManager(_StateManager):
    def save_checkpoint(self, **kwargs):
        pass


class _Dlq:
    def __init__(self):
        self.entries = []

    def transition(self, entry):
        self.entries.append(entry)


def _process_task(agent, recorder):
    app = Celery("test-status-sync")
    app.conf.task_always_eager = True
    return tasks.register_process_accepted_incident_task(
        tasks.RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=0),
        tasks.IntegrationSeams(agent=agent, state_recorder=recorder, dlq=_Dlq()),
        app=app,
    )


def test_run_is_in_progress_in_servicenow_before_the_agent_starts(monkeypatch):
    events = []
    fake = FakeRegistry(events=events)
    monkeypatch.setattr("src.agent.tools.registry.DEFAULT_TOOL_REGISTRY", fake)

    class Agent:
        def execute(self, incident):
            events.append(("agent", None))
            return {"action_taken": "resolved_automatically", "outputs": {"resolution": "Restart"}}

    recorder = StateManagerTaskRecorder(
        ExecutionContext("exec-5", 1), "celery_worker",
        state_manager_factory=lambda: (_FullStateManager(), lambda: None),
    )
    _process_task(Agent(), recorder).apply(args=(PAYLOAD,)).get()

    assert events == [
        ("write_ai_fields", "in_progress"),  # before the graph, so it can never land last
        ("agent", None),
        ("write_ai_fields", "complete"),
    ]


def test_in_progress_write_failing_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr("src.agent.tools.registry.DEFAULT_TOOL_REGISTRY", FakeRegistry(fail={"write_ai_fields"}))
    runtime_integration.sync_servicenow_in_progress(PAYLOAD, "exec-6")  # does not raise


def _resume_task(agent, registry_incident=PAYLOAD):
    app = Celery("test-resume-status")
    app.conf.task_always_eager = True
    manager = _StateManager()
    return tasks.register_resume_incident_task(
        tasks.RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=0),
        app,
        agent=agent,
        state_manager_factory=lambda: (manager, lambda: None),
        incident_lookup=lambda execution_id: registry_incident,
    ), manager


def test_successful_resume_leaves_the_final_state_to_act(registry):
    class Agent:
        def resume(self, execution_id, decision):
            return {"action_taken": "approved_by_human", "servicenow_write": "written"}

    task, _ = _resume_task(Agent())
    task.apply(args=["exec-7", {"decision": "approve"}]).get()

    assert registry.calls == []  # act and the completion sync write the outcome


def test_failed_resume_is_marked_failed_in_servicenow(registry):
    class Agent:
        def resume(self, execution_id, decision):
            raise ValueError("checkpoint gone")

    task, manager = _resume_task(Agent())
    outcome = task.apply(args=["exec-8", {"decision": "approve"}])

    assert outcome.failed()
    assert manager.statuses == [("exec-8", "failed")]
    assert _states(registry) == [
        ("write_ai_fields", "failed"),  # not stuck on Awaiting Approval
        ("write_work_note", None),
    ]
    assert "ValueError: checkpoint gone" in registry.calls[-1][2]["note"]


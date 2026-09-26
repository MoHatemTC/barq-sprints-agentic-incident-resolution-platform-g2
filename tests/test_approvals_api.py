"""tests for S3.4 approvals API list/show paused executions and resume the Same checkpointed execution through Command(resume=...)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from celery import Celery
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from src.agent.checkpointer import is_paused, thread_config
from src.agent.graph import create_graph
from src.api.app import create_app
from src.api.dependencies import get_db_session, get_redis
from src.api.routers import approvals
from src.workers import tasks
from src.workers.retry_policy import RetryPolicy
from src.workers.runtime_integration import execution_status_for


pytestmark = pytest.mark.usefixtures("hermetic_llm")


BRIEF = {
    "what_happened": "Payroll database is down.",
    "why_stopped": "The incident was classified as high risk.",
    "proposed_action": "No action was drafted.",
    "reviewer_question": "Should the agent act on a production outage?",
}


def _payload(eid):
    return {
        "gate": "high_risk",
        "reason_text": "The incident was classified as high risk",
        "incident": {"number": "INC0001", "short_description": "Payroll DB down"},
        "evidence": [],
        "draft": {"diagnosis": None, "resolution": None},
        "verdicts": {"risk": "high", "confidence": None, "critic_verdict": None, "guardrail": None},
        "created_at": "2026-09-25T10:00:00+00:00",
    }


def _pause(graph, eid, brief=BRIEF):
    """Put a run at prepare_review's output, then let interrupt() really pause it."""
    graph.update_state(
        thread_config(eid),
        {
            "execution_id": eid,
            "incident_number": "INC0001",
            "incident_payload": {"description": "payroll db down"},  # no sys_id: act skips the write
            "risk": "high",
            "gate": "high_risk",
            "interrupt_payload": _payload(eid),
            "approval_brief": brief,
            "human_review_required": True,
        },
        as_node="prepare_review",
    )
    graph.invoke(None, config=thread_config(eid))
    assert is_paused(graph.get_state(thread_config(eid)))


class FakeStore:
    def __init__(self, awaiting=()):
        self.awaiting = list(awaiting)
        self.claimed = set()
        self.records = []

    def awaiting_execution_ids(self):
        return list(self.awaiting)

    def claim_decision(self, execution_id):
        if execution_id in self.claimed:
            return False
        self.claimed.add(execution_id)
        return True

    def record_decision(self, execution_id, evidence, decision, reviewer):
        self.records.append((execution_id, json.loads(evidence), decision, reviewer))

    def recorded_decision(self, execution_id):
        for eid, evidence, decision, reviewer in reversed(self.records):
            if eid == execution_id:
                return {
                    "status": decision,
                    "reviewer": reviewer,
                    "rationale": evidence["rationale"],
                    "decided_at": datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
                }
        return None


class SyncDispatcher:
    """Resumes inline, like the worker's resume task would."""

    def __init__(self, graph, resume=True, error=None):
        self.graph, self.resume, self.error = graph, resume, error
        self.calls = []

    def __call__(self, execution_id, decision):
        self.calls.append((execution_id, decision))
        if self.error:
            raise self.error
        if self.resume:
            tasks.continue_run(self.graph, execution_id, decision)


@pytest.fixture
def graph():
    return create_graph().compile(checkpointer=MemorySaver())


@pytest.fixture
def setup(graph):
    store = FakeStore()
    dispatcher = SyncDispatcher(graph)
    app = create_app()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[approvals.get_approval_store] = lambda: store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: graph
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: dispatcher

    with TestClient(app) as client:
        yield client, store, dispatcher
    app.dependency_overrides.clear()


# tests for GET

def test_list_shows_only_paused_runs(setup, graph):
    client, store, _ = setup
    _pause(graph, "paused-1")
    _pause(graph, "done-1")
    tasks.continue_run(graph, "done-1", {"decision": "approve"})
    store.awaiting = ["paused-1", "done-1", "unknown-1"]

    body = client.get("/api/v1/approvals").json()

    assert body["total"] == 1
    item = body["items"][0]
    assert item["approval_id"] == item["execution_id"] == "paused-1"
    assert item["gate"] == "high_risk"
    assert item["brief_status"] == "generated"
    assert item["status"] == "pending"


def test_detail_has_brief_and_raw_payload(setup, graph):
    client, _, _ = setup
    _pause(graph, "d-1")

    body = client.get("/api/v1/approvals/d-1").json()

    assert body["brief"] == BRIEF
    assert body["payload"] == _payload("d-1")
    assert body["reason_text"] == "The incident was classified as high risk"


def test_detail_without_brief_still_returns_raw_payload(setup, graph):
    client, _, _ = setup
    _pause(graph, "d-2", brief=None)

    body = client.get("/api/v1/approvals/d-2").json()

    assert body["brief"] is None
    assert body["brief_status"] == "unavailable"
    assert body["payload"]["gate"] == "high_risk"


def test_detail_unknown_is_404_and_finished_is_409(setup, graph):
    client, _, _ = setup
    _pause(graph, "d-3")
    tasks.continue_run(graph, "d-3", {"decision": "approve"})

    assert client.get("/api/v1/approvals/nope").status_code == 404
    assert client.get("/api/v1/approvals/d-3").status_code == 409


# POST decide tests

def test_approve_persists_then_resumes_same_run(setup, graph):
    client, store, dispatcher = setup
    _pause(graph, "a-1")

    response = client.post(
        "/api/v1/approvals/a-1/decide",
        json={"action": "approve", "reviewer": "sarah", "rationale": "safe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved" and body["resumed"] is True

    # decision persisted with what the reviewer saw
    execution_id, evidence, decision, reviewer = store.records[0]
    assert (execution_id, decision, reviewer) == ("a-1", "approved", "sarah")
    assert evidence["payload"] == _payload("a-1")
    assert evidence["brief"] == BRIEF

    assert dispatcher.calls == [("a-1", {"decision": "approve", "reviewer": "sarah", "comment": "safe"})]

    # the SAME thread finished through act
    snapshot = graph.get_state(thread_config("a-1"))
    assert snapshot.next == ()
    assert snapshot.values["action_taken"] == "approved_by_human"
    assert snapshot.values["gate"] == "high_risk"


def test_reject_resumes_to_rejection(setup, graph):
    client, store, _ = setup
    _pause(graph, "r-1")

    response = client.post("/api/v1/approvals/r-1/decide", json={"action": "reject", "reviewer": "bob"})

    assert response.json()["status"] == "rejected"
    assert store.records[0][2] == "rejected"
    assert graph.get_state(thread_config("r-1")).values["action_taken"] == "rejected_by_human"


def test_second_decision_is_refused(setup, graph):
    client, _, dispatcher = setup
    _pause(graph, "twice")
    dispatcher.resume = False  # the worker has not picked it up yet: still paused

    first = client.post("/api/v1/approvals/twice/decide", json={"action": "approve", "reviewer": "a"})
    second = client.post("/api/v1/approvals/twice/decide", json={"action": "reject", "reviewer": "b"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(dispatcher.calls) == 1


def test_decide_on_finished_run_is_409(setup, graph):
    client, store, _ = setup
    _pause(graph, "fin")
    tasks.continue_run(graph, "fin", {"decision": "approve"})

    response = client.post("/api/v1/approvals/fin/decide", json={"action": "approve", "reviewer": "a"})

    assert response.status_code == 409
    assert store.records == []


def test_decide_unknown_is_404_and_bad_action_is_422(setup):
    client, _, _ = setup
    assert client.post("/api/v1/approvals/nope/decide", json={"action": "approve", "reviewer": "a"}).status_code == 404
    assert client.post("/api/v1/approvals/nope/decide", json={"action": "maybe", "reviewer": "a"}).status_code == 422


def test_dispatch_failure_is_503_but_decision_is_kept(setup, graph):
    client, store, dispatcher = setup
    _pause(graph, "down")
    dispatcher.error = ConnectionError("redis down")

    response = client.post("/api/v1/approvals/down/decide", json={"action": "approve", "reviewer": "a"})

    assert response.status_code == 503
    assert store.records[0][0] == "down"


def test_retry_after_dispatch_failure_resends_and_resumes(setup, graph):
    """Mentor note: broker down after the decision was stored used to leave the run
    paused for good (retry got 409). The same retry now re-sends the stored decision."""
    client, store, dispatcher = setup
    _pause(graph, "down-2")
    dispatcher.error = ConnectionError("redis down")
    body = {"action": "approve", "reviewer": "sarah", "rationale": "safe"}

    assert client.post("/api/v1/approvals/down-2/decide", json=body).status_code == 503

    dispatcher.error = None  # broker back
    retry = client.post("/api/v1/approvals/down-2/decide", json=body)

    assert retry.status_code == 200
    assert retry.json()["status"] == "approved" and retry.json()["resumed"] is True
    assert len(store.records) == 1  # the decision is not recorded twice
    assert dispatcher.calls[-1] == ("down-2", {"decision": "approve", "reviewer": "sarah", "comment": "safe"})
    assert graph.get_state(thread_config("down-2")).values["action_taken"] == "approved_by_human"


def test_retry_with_other_decision_is_still_refused(setup, graph):
    """The retry path re-sends the stored decision only; it cannot flip it."""
    client, _, dispatcher = setup
    _pause(graph, "down-3")
    dispatcher.error = ConnectionError("redis down")
    client.post("/api/v1/approvals/down-3/decide", json={"action": "approve", "reviewer": "sarah"})
    dispatcher.error = None

    other_action = client.post("/api/v1/approvals/down-3/decide", json={"action": "reject", "reviewer": "sarah"})
    other_reviewer = client.post("/api/v1/approvals/down-3/decide", json={"action": "approve", "reviewer": "bob"})

    assert other_action.status_code == 409
    assert other_reviewer.status_code == 409
    assert len(dispatcher.calls) == 1  # only the failed first attempt
    assert is_paused(graph.get_state(thread_config("down-3")))


def test_retry_while_broker_still_down_is_503_again(setup, graph):
    client, store, dispatcher = setup
    _pause(graph, "down-4")
    dispatcher.error = ConnectionError("redis down")
    body = {"action": "reject", "reviewer": "bob"}

    assert client.post("/api/v1/approvals/down-4/decide", json=body).status_code == 503
    assert client.post("/api/v1/approvals/down-4/decide", json=body).status_code == 503
    assert len(store.records) == 1


#  worker side tests

def test_status_for_paused_and_finished_results():
    assert execution_status_for({"__interrupt__": ["x"]}) == "awaiting_approval"
    assert execution_status_for({"action_taken": "resolved_automatically"}) == "succeeded"
    assert execution_status_for(None) == "succeeded"


def test_continue_run_finishes_a_run_stopped_before_act(graph):
    _pause(graph, "crash")
    graph.invoke(tasks.Command(resume={"decision": "approve"}), config=thread_config("crash"))
    # simulate a crash that left the thread at act: rewind to interrupt's output
    graph.update_state(thread_config("crash"), {"human_decision": {"decision": "approve"}}, as_node="interrupt")
    assert graph.get_state(thread_config("crash")).next == ("act",)

    result = tasks.continue_run(graph, "crash")

    assert result["action_taken"] == "approved_by_human"


def test_resume_task_name_matches_api():
    assert approvals.RESUME_TASK_NAME == tasks.RESUME_TASK_NAME


class FakeStateManager:
    def __init__(self):
        self.statuses, self.failures = [], []

    def update_execution_status(self, execution_id, status):
        self.statuses.append((execution_id, status))

    def record_failure(self, **kwargs):
        self.failures.append(kwargs)


def _resume_task(agent, manager):
    app = Celery("test-resume")
    app.conf.task_always_eager = True
    return tasks.register_resume_incident_task(
        RetryPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=0),
        app,
        agent=agent,
        state_manager_factory=lambda: (manager, lambda: None),
    )


def test_resume_task_records_final_status():
    class Agent:
        def resume(self, execution_id, decision):
            return {"action_taken": "approved_by_human", "servicenow_write": "written"}

    manager = FakeStateManager()
    result = _resume_task(Agent(), manager).apply(args=["e-1", {"decision": "approve"}]).get()

    assert result == {"execution_id": "e-1", "action_taken": "approved_by_human", "servicenow_write": "written"}
    assert manager.statuses == [("e-1", "succeeded")]


def test_resume_task_marks_failure():
    class Agent:
        def resume(self, execution_id, decision):
            raise ValueError("boom")

    manager = FakeStateManager()
    outcome = _resume_task(Agent(), manager).apply(args=["e-2", {"decision": "approve"}])

    assert outcome.failed()
    assert manager.statuses == [("e-2", "failed")]
    assert manager.failures[0]["failing_node"] == "resume"

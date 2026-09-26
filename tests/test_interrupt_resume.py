"""S3.4: human review pauses the graph with interrupt() and resumes the SAME checkpointed run through Command(resume=...)."""

from unittest.mock import patch, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.agent.graph import create_graph, route_after_confidence
from src.agent.nodes.interrupt import normalize_decision
from src.agent.nodes.prepare_review import detect_gate

pytestmark = pytest.mark.usefixtures("hermetic_llm")


HIGH_RISK = {"description": "this is a high-risk task"}


def _chunk():
    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "p1"
    chunk.text = "Reset the VPN credentials"
    chunk.score = 5.0
    return chunk


def _graph():
    return create_graph().compile(checkpointer=MemorySaver())


def _cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _start(graph, thread_id, incident):
    return graph.invoke(
        {"execution_id": thread_id, "incident_number": "INC0001", "incident_payload": incident},
        config=_cfg(thread_id),
    )


# pause tests

def test_high_risk_pauses_at_interrupt_with_payload():
    graph = _graph()
    result = _start(graph, "t-pause", HIGH_RISK)

    # invoke returns early and surfaces the interrupt payload
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "high_risk"
    assert payload["incident"]["description"] == HIGH_RISK["description"]
    assert payload["verdicts"]["risk"] == "high"
    assert payload["reason_text"]

    # the checkpoint is parked at interrupt, act has not run
    snapshot = graph.get_state(_cfg("t-pause"))
    assert snapshot.next == ("interrupt",)
    assert snapshot.values.get("action_taken") is None
    assert snapshot.values["interrupt_payload"] == payload


@patch("src.agent.nodes.retrieve.search", return_value=[_chunk()])
def test_normal_incident_does_not_pause(mock_search):
    graph = _graph()
    result = _start(graph, "t-auto", {"description": "normal issue"})

    assert "__interrupt__" not in result
    assert result["action_taken"] == "resolved_automatically"
    assert graph.get_state(_cfg("t-auto")).next == ()


# resume tests

def test_approve_resumes_same_run_to_act():
    graph = _graph()
    _start(graph, "t-approve", HIGH_RISK)

    result = graph.invoke(
        Command(resume={"decision": "approve", "reviewer": "alice", "comment": "ok"}),
        config=_cfg("t-approve"),
    )

    assert result["action_taken"] == "approved_by_human"
    assert result["human_decision"] == {"decision": "approve", "reviewer": "alice", "comment": "ok"}
    # same thread: state from before the pause is still there
    assert result["risk"] == "high"
    assert result["gate"] == "high_risk"
    assert graph.get_state(_cfg("t-approve")).next == ()


def test_reject_resumes_to_rejection():
    graph = _graph()
    _start(graph, "t-reject", HIGH_RISK)

    result = graph.invoke(
        Command(resume={"decision": "reject", "reviewer": "bob"}), config=_cfg("t-reject")
    )

    assert result["action_taken"] == "rejected_by_human"
    assert result["human_decision"]["reviewer"] == "bob"


def test_payload_is_built_once_across_pause_and_resume():
    """prepare_review completes before the pause, so resume must not rebuild it."""
    graph = _graph()
    first = _start(graph, "t-once", HIGH_RISK)["__interrupt__"][0].value

    result = graph.invoke(Command(resume={"decision": "approve"}), config=_cfg("t-once"))

    assert result["interrupt_payload"]["created_at"] == first["created_at"]


# gates tests

EVIDENCE = [{"id": "KB1"}]


@pytest.mark.parametrize(
    "state, gate",
    [
        ({"outputs": {"eligibility": "invalid"}}, "invalid_incident"),
        ({"outputs": {"eligibility": "valid"}, "risk": "high"}, "high_risk"),
        ({"risk": "low", "retrieval_failed": True, "retrieved_evidence": []}, "retrieval_failed"),
        ({"risk": "low", "retrieved_evidence": []}, "no_evidence"),
        ({"risk": "low", "retrieved_evidence": EVIDENCE, "critic_exhausted": True}, "critic_exhausted"),
        ({"risk": "low", "retrieved_evidence": EVIDENCE, "action_taken": "blocked_by_guardrail"}, "safety_blocked"),
        ({"risk": "low", "retrieved_evidence": EVIDENCE, "confidence": 0.2}, "low_confidence"),
    ],
)
def test_detect_gate(state, gate):
    assert detect_gate(state) == gate


def test_valid_high_risk_ticket_reports_high_risk_gate():
    """validate always sets eligibility; a valid ticket stopped by route_after_risk
    must be reported as high_risk (mentor bug 1)."""
    state = {"outputs": {"eligibility": "valid"}, "risk": "high"}
    assert detect_gate(state) == "high_risk"


@patch("src.agent.nodes.determine_risk.get_llm")
@patch("src.agent.nodes.classify.get_llm")
@patch("src.agent.nodes.validate.get_llm")
def test_invalid_ticket_stops_before_classify(mock_validate_llm, mock_classify_llm, mock_risk_llm):
    """route_after_validate (mentor bug 2): an invalid ticket, even a high-risk one,
    goes to review before classify/determine_risk spend LLM calls, and the gate
    names the edge that actually routed it."""
    mock_validate_llm.return_value.invoke.return_value = MagicMock(content="invalid")
    graph = _graph()
    result = _start(graph, "t-invalid", HIGH_RISK)

    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "invalid_incident"
    assert payload["verdicts"]["risk"] is None
    mock_classify_llm.assert_not_called()
    mock_risk_llm.assert_not_called()


@pytest.mark.parametrize(
    "state",
    [
        {"confidence": 0.95, "critic_exhausted": True},
        {"confidence": 0.95, "action_taken": "blocked_by_guardrail"},
        {"confidence": 0.1},
    ],
)
def test_confidence_router_sends_human_cases_to_review(state):
    assert route_after_confidence(state) == "prepare_review"


def test_confidence_router_lets_confident_runs_act():
    assert route_after_confidence({"confidence": 0.95}) == "act"


# decision handling -tests

@pytest.mark.parametrize("value", [None, "approve", {}, {"decision": "APPROVE"}, {"decision": "maybe"}])
def test_unclear_decision_counts_as_reject(value):
    assert normalize_decision(value)["decision"] == "reject"

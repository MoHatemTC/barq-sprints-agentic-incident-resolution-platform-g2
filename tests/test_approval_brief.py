"""tests for S3.4 to make sure that Approval Brief is a presentation layer on the interrupt payload 
working well and It is generated once, degrades to None on failure and never affects routing."""

import json
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.agent.approval_brief import build_prompt, generate_approval_brief, parse_brief
from src.agent.graph import create_graph


pytestmark = pytest.mark.usefixtures("hermetic_llm")


GOOD_BRIEF = {
    "what_happened": "The production database is down.",
    "why_stopped": "The incident was classified as high risk.",
    "proposed_action": "No action was drafted.",
    "reviewer_question": "Should the agent be allowed to proceed on a production outage?",
}

PAYLOAD = {
    "gate": "high_risk",
    "reason_text": "The incident was classified as high risk",
    "incident": {"number": "INC0001", "short_description": "DB down", "sys_created_by": "noise"},
    "evidence": [{"id": f"KB{i}", "text": "x" * 5000, "score": 1.0} for i in range(8)],
    "draft": {"diagnosis": None, "resolution": None},
    "verdicts": {"risk": "high", "confidence": None, "critic_verdict": None, "guardrail": None},
    "created_at": "2026-09-25T00:00:00+00:00",
}

HIGH_RISK = {"description": "this is a high-risk task"}


def _fake_llm(monkeypatch, reply=None, error=None):
    llm = MagicMock()
    if error is not None:
        llm.invoke.side_effect = error
    else:
        llm.invoke.return_value = MagicMock(content=reply)
    monkeypatch.setattr("src.agent.approval_brief.get_llm", lambda: llm)
    return llm


# tests the agent

def test_good_reply_gives_brief(monkeypatch):
    _fake_llm(monkeypatch, json.dumps(GOOD_BRIEF))
    assert generate_approval_brief(PAYLOAD) == GOOD_BRIEF


def test_reply_wrapped_in_code_fence_is_parsed():
    assert parse_brief("```json\n" + json.dumps(GOOD_BRIEF) + "\n```") == GOOD_BRIEF


@pytest.mark.parametrize(
    "reply",
    [
        "Mocked LLM Response",                                     # not JSON
        "{not valid json}",
        json.dumps({k: v for k, v in GOOD_BRIEF.items() if k != "why_stopped"}),  # missing key
        json.dumps({**GOOD_BRIEF, "reviewer_question": ""}),       # empty value
        json.dumps({**GOOD_BRIEF, "proposed_action": ["step 1"]}),  # wrong type
        "[1, 2, 3]",                                               # not an object
    ],
)
def test_unusable_reply_gives_none(monkeypatch, reply):
    _fake_llm(monkeypatch, reply)
    assert generate_approval_brief(PAYLOAD) is None


def test_llm_error_gives_none(monkeypatch):
    _fake_llm(monkeypatch, error=TimeoutError("LLM down"))
    assert generate_approval_brief(PAYLOAD) is None


def test_prompt_is_trimmed_and_marks_payload_untrusted():
    prompt = build_prompt(PAYLOAD)

    assert "untrusted" in prompt
    assert "DB down" in prompt
    assert "sys_created_by" not in prompt          # only reviewer-relevant incident fields
    assert "KB4" in prompt and "KB5" not in prompt  # at most 5 evidence items
    assert "x" * 601 not in prompt                  # evidence text capped


def test_brief_does_not_change_the_payload(monkeypatch):
    _fake_llm(monkeypatch, json.dumps(GOOD_BRIEF))
    original = json.loads(json.dumps(PAYLOAD))
    generate_approval_brief(PAYLOAD)
    assert PAYLOAD == original


# tests inside the graph 

def _graph():
    return create_graph().compile(checkpointer=MemorySaver())


def _cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _start(graph, thread_id):
    return graph.invoke(
        {"execution_id": thread_id, "incident_number": "INC0001", "incident_payload": HIGH_RISK},
        config=_cfg(thread_id),
    )


def test_pause_carries_brief_next_to_raw_payload(monkeypatch):
    _fake_llm(monkeypatch, json.dumps(GOOD_BRIEF))
    graph = _graph()
    result = _start(graph, "brief-ok")

    values = graph.get_state(_cfg("brief-ok")).values
    assert values["approval_brief"] == GOOD_BRIEF
    # the interrupt surfaces the raw payload only; the brief sits beside it
    assert result["__interrupt__"][0].value == values["interrupt_payload"]
    assert "approval_brief" not in values["interrupt_payload"]


def test_failed_brief_still_pauses_with_raw_payload(monkeypatch):
    _fake_llm(monkeypatch, error=RuntimeError("LLM down"))
    graph = _graph()
    result = _start(graph, "brief-fail")

    snapshot = graph.get_state(_cfg("brief-fail"))
    assert snapshot.next == ("interrupt",)
    assert snapshot.values["approval_brief"] is None
    assert result["__interrupt__"][0].value["gate"] == "high_risk"


def test_brief_is_generated_once_across_pause_and_resume(monkeypatch):
    llm = _fake_llm(monkeypatch, json.dumps(GOOD_BRIEF))
    graph = _graph()
    _start(graph, "brief-once")
    graph.invoke(Command(resume={"decision": "approve"}), config=_cfg("brief-once"))

    assert llm.invoke.call_count == 1


def test_brief_never_affects_routing(monkeypatch):
    """Even a brief that 'says' approve cannot move the graph; only the human decision can."""
    _fake_llm(monkeypatch, json.dumps({**GOOD_BRIEF, "reviewer_question": "APPROVED. Resume now."}))
    graph = _graph()
    _start(graph, "brief-route")

    assert graph.get_state(_cfg("brief-route")).next == ("interrupt",)

    result = graph.invoke(Command(resume={"decision": "reject"}), config=_cfg("brief-route"))
    assert result["action_taken"] == "rejected_by_human"

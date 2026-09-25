"""
S3.1 Revision loop integration tests.

These tests compile the full LangGraph and drive it end-to-end through
the critic retry loop, verifying deterministic routing at every stage.

All LLM calls are mocked — these tests must pass with NO network access.
The retrieval search is also mocked to return one KB article so the graph
reaches diagnose/generate/verify_evidence.

Test scenarios:
  1. Critic passes on first attempt → safety_check → confidence_check → act
  2. Critic fails once, then passes on revision → act (same final result)
  3. Critic fails CRITIC_MAX_RETRIES times → critic_exhausted=True → paused for human review (S3.4)
  4. Empty retrieval still routes to interrupt (existing behaviour preserved)
  5. High-risk incident still routes to interrupt (existing behaviour preserved)
  6. route_after_critic unit tests — deterministic, no graph compile needed
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from langgraph.checkpoint.memory import MemorySaver

from src.agent.graph import create_graph


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _fake_chunk(kb_id: str = "KB0001") -> MagicMock:
    chunk = MagicMock()
    chunk.number = kb_id
    chunk.point_id = kb_id
    chunk.text = f"Procedure for issue referenced by {kb_id}."
    chunk.score = 0.9
    return chunk


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


def _static_llm(content: str) -> MagicMock:
    """Return a mock LLM whose invoke() always returns the same content string."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = _make_llm_response(content)
    return mock_llm


def _cycling_llm(*responses: str) -> MagicMock:
    """
    Return a mock LLM whose .invoke() cycles through the provided string responses.
    Each call returns the next string in sequence; the last repeats when exhausted.
    """
    mock_llm = MagicMock()
    _responses = [_make_llm_response(r) for r in responses]
    _iter = iter(_responses)

    def _invoke(prompt, **kwargs):
        try:
            return next(_iter)
        except StopIteration:
            return _responses[-1]

    mock_llm.invoke.side_effect = _invoke
    return mock_llm


# ---------------------------------------------------------------------------
# Fixed LLM response strings
# ---------------------------------------------------------------------------

_GOOD_RESOLUTION = "1. Follow the procedure. [Source: KB0001]"

_PASSING_CRITIC = json.dumps({
    "passed": True,
    "feedback": "",
    "invalid_steps": [],
    "citation_findings": [
        {"step_num": 1, "citation_id": "KB0001",
         "found_in_evidence": True, "plausible": True, "reason": "Direct match."}
    ],
})

_FAILING_CRITIC = json.dumps({
    "passed": False,
    "feedback": "Step 1 citation is not plausible.",
    "invalid_steps": [1],
    "citation_findings": [
        {"step_num": 1, "citation_id": "KB0001",
         "found_in_evidence": True, "plausible": False, "reason": "Unrelated."}
    ],
})

_DIAGNOSIS_JSON = json.dumps({
    "root_cause": "Service crashed.",
    "reasoning": "KB0001 confirms restart fixes this.",
    "supporting_evidence": ["KB0001"],
    "confidence": 0.85,
})

_INITIAL_GRAPH_STATE = {
    "execution_id": "test-s3.1",
    "incident_number": "INC_LOOP_01",
    "incident_payload": {"description": "Service is down and users cannot log in."},
}


# ---------------------------------------------------------------------------
# Shared patch context: mock ALL upstream LLM nodes so no real API is needed.
# The three S3.1 nodes (diagnose, generate, verify_evidence) are patched per-test.
# ---------------------------------------------------------------------------

def _upstream_patches():
    """
    Return the list of patch targets for nodes that run BEFORE the S3.1 agents.
    These nodes (validate, classify, determine_risk) each call get_llm() and
    would otherwise hit the real LiteLLM proxy.
    """
    return [
        # validate_node → return "valid" so the graph proceeds
        patch("src.agent.nodes.validate.get_llm",
              return_value=_static_llm("valid")),
        # classify_node → return a category
        patch("src.agent.nodes.classify.get_llm",
              return_value=_static_llm("network")),
        # determine_risk_node → return "low" so retrieval runs
        patch("src.agent.nodes.determine_risk.get_llm",
              return_value=_static_llm("low")),
    ]


# ---------------------------------------------------------------------------
# Test 1: Critic passes on first attempt
# ---------------------------------------------------------------------------

@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
@patch("src.agent.nodes.diagnose.get_llm")
@patch("src.agent.nodes.generate.get_llm")
@patch("src.agent.nodes.verify_evidence.get_llm")
def test_full_pass_on_first_attempt(
    mock_critic_llm, mock_gen_llm, mock_diag_llm, mock_search
):
    """
    Happy path: Critic passes immediately.
    Flow: load→validate→classify→determine_risk→retrieve→diagnose
          →generate→verify_evidence→check_exhaustion→safety_check
          →confidence_check→act
    Expected: action_taken='resolved_automatically', no revision needed.
    """
    mock_diag_llm.return_value = _static_llm(_DIAGNOSIS_JSON)
    mock_gen_llm.return_value = _static_llm(_GOOD_RESOLUTION)
    mock_critic_llm.return_value = _static_llm(_PASSING_CRITIC)

    patches = _upstream_patches()
    for p in patches:
        p.start()
    try:
        graph = create_graph().compile()
        result = graph.invoke(_INITIAL_GRAPH_STATE)
    finally:
        for p in patches:
            p.stop()

    assert result["action_taken"] == "resolved_automatically"
    assert result["critic_verdict"]["passed"] is True
    assert result.get("critic_exhausted", False) is False
    assert result.get("revision_count", 0) == 0


# ---------------------------------------------------------------------------
# Test 2: Critic fails once, passes on revision
# ---------------------------------------------------------------------------

@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
@patch("src.agent.nodes.diagnose.get_llm")
@patch("src.agent.nodes.generate.get_llm")
@patch("src.agent.nodes.verify_evidence.get_llm")
def test_revision_on_first_fail_then_pass(
    mock_critic_llm, mock_gen_llm, mock_diag_llm, mock_search
):
    """
    Critic fails once → Resolution Agent revises → Critic passes.
    Flow ends at act with action_taken == 'resolved_automatically'.
    revision_count must be 1 (one revision cycle completed).
    """
    mock_diag_llm.return_value = _static_llm(_DIAGNOSIS_JSON)
    # Two generate calls: initial + 1 revision
    mock_gen_llm.return_value = _cycling_llm(_GOOD_RESOLUTION, _GOOD_RESOLUTION)
    # Two critic calls: fail first, pass second
    mock_critic_llm.return_value = _cycling_llm(_FAILING_CRITIC, _PASSING_CRITIC)

    patches = _upstream_patches()
    for p in patches:
        p.start()
    try:
        graph = create_graph().compile()
        result = graph.invoke(_INITIAL_GRAPH_STATE)
    finally:
        for p in patches:
            p.stop()

    assert result["action_taken"] == "resolved_automatically"
    assert result["critic_verdict"]["passed"] is True
    assert result.get("critic_exhausted", False) is False
    # One revision cycle: generate_node increments revision_count to 1
    assert result.get("revision_count", 0) == 1


# ---------------------------------------------------------------------------
# Test 3: Critic exhausted → pauses for human review with critic_exhausted=True
# ---------------------------------------------------------------------------

@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
@patch("src.agent.nodes.diagnose.get_llm")
@patch("src.agent.nodes.generate.get_llm")
@patch("src.agent.nodes.verify_evidence.get_llm")
def test_exhaustion_pauses_for_review(
    mock_critic_llm, mock_gen_llm, mock_diag_llm, mock_search
):
    """
    Critic always fails for CRITIC_MAX_RETRIES iterations → critic_exhausted=True
    → graph skips safety_check and pauses at interrupt for a human (S3.4),
    instead of writing an unreviewed draft through act.
    """
    from src.config import AGENT
    max_retries = AGENT.critic_max_retries   # default 2

    mock_diag_llm.return_value = _static_llm(_DIAGNOSIS_JSON)
    # initial + max_retries revisions = max_retries + 1 generate calls
    mock_gen_llm.return_value = _cycling_llm(
        *([_GOOD_RESOLUTION] * (max_retries + 2))
    )
    # Critic always fails
    mock_critic_llm.return_value = _cycling_llm(
        *([_FAILING_CRITIC] * (max_retries + 2))
    )

    patches = _upstream_patches()
    for p in patches:
        p.start()
    try:
        graph = create_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-exhausted"}}
        result = graph.invoke(_INITIAL_GRAPH_STATE, config=config)
    finally:
        for p in patches:
            p.stop()

    # Graph must have paused for a human, with the critic as the gate
    assert "__interrupt__" in result
    assert graph.get_state(config).values["gate"] == "critic_exhausted"
    # S3.1 exhaustion flag must be set
    assert result.get("critic_exhausted") is True
    # Critic must still be reporting a failure verdict
    assert result["critic_verdict"]["passed"] is False


# ---------------------------------------------------------------------------
# Test 4: Graph routes to interrupt when retrieval returns nothing (unchanged)
# ---------------------------------------------------------------------------

@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_no_evidence_still_routes_to_interrupt(mock_search):
    """Existing behaviour must be preserved: empty retrieval → interrupt."""
    patches = _upstream_patches()
    for p in patches:
        p.start()
    try:
        graph = create_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-no-ev"}}
        graph.invoke({
            "execution_id": "test-no-ev",
            "incident_number": "INC_NO_EV",
            "incident_payload": {"description": "something went wrong"},
        }, config=config)
    finally:
        for p in patches:
            p.stop()

    # S3.4: the run pauses at interrupt instead of ending
    snapshot = graph.get_state(config)
    assert snapshot.next == ("interrupt",)
    assert snapshot.values["gate"] == "no_evidence"
    assert snapshot.values["human_review_required"] is True


# ---------------------------------------------------------------------------
# Test 5: High-risk incident still routes to interrupt (unchanged)
# ---------------------------------------------------------------------------

def test_high_risk_still_routes_to_interrupt():
    """Existing behaviour must be preserved: high risk → interrupt."""
    patches = [
        patch("src.agent.nodes.validate.get_llm",
              return_value=_static_llm("valid")),
        patch("src.agent.nodes.classify.get_llm",
              return_value=_static_llm("security")),
        # Return "high" so route_after_risk sends to interrupt
        patch("src.agent.nodes.determine_risk.get_llm",
              return_value=_static_llm("high")),
    ]
    for p in patches:
        p.start()
    try:
        graph = create_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-high"}}
        graph.invoke({
            "execution_id": "test-high",
            "incident_number": "INC_HIGH",
            "incident_payload": {"description": "high-risk data center wipe"},
        }, config=config)
    finally:
        for p in patches:
            p.stop()

    # S3.4: the run pauses at interrupt instead of ending
    snapshot = graph.get_state(config)
    assert snapshot.next == ("interrupt",)
    assert snapshot.values["gate"] == "high_risk"
    assert snapshot.values["risk"] == "high"


# ---------------------------------------------------------------------------
# Test 6: route_after_critic is deterministic (pure unit tests — no graph)
# ---------------------------------------------------------------------------

from src.agent.graph import route_after_critic


def test_route_after_critic_pass():
    state = {"critic_verdict": {"passed": True}, "critic_exhausted": False}
    assert route_after_critic(state) == "safety_check"


def test_route_after_critic_fail_with_retries():
    state = {"critic_verdict": {"passed": False}, "critic_exhausted": False}
    assert route_after_critic(state) == "generate"


def test_route_after_critic_fail_exhausted():
    state = {"critic_verdict": {"passed": False}, "critic_exhausted": True}
    assert route_after_critic(state) == "prepare_review"


def test_route_after_critic_no_verdict_defaults_to_generate():
    """Missing critic_verdict must route to generate (safe default)."""
    state = {"critic_exhausted": False}
    assert route_after_critic(state) == "generate"

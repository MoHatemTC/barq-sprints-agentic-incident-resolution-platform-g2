import json
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

from langgraph.checkpoint.memory import MemorySaver

from src.agent.graph import create_graph


def _paused(graph, thread_id):
    """State of a run that paused at interrupt() for human review."""
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    assert snapshot.next == ("interrupt",)
    return snapshot.values


def _fake_chunk():
    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "p1"
    chunk.text = "Reset the VPN credentials"
    chunk.score = 5.0
    return chunk


def _build_static_llm(content):
    mock_llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    mock_llm.invoke.return_value = resp
    return mock_llm


@contextmanager
def _mock_routing_agents(risk="low", include_resolution_agents=False):
    """Keep graph-routing tests independent from the external LLM proxy."""
    patches = [
        patch("src.agent.nodes.validate.get_llm", return_value=_build_static_llm("valid")),
        patch("src.agent.nodes.classify.get_llm", return_value=_build_static_llm("network")),
        patch("src.agent.nodes.determine_risk.get_llm", return_value=_build_static_llm(risk)),
    ]
    if include_resolution_agents:
        patches.extend([
            patch(
                "src.agent.nodes.diagnose.get_llm",
                return_value=_build_static_llm(json.dumps({
                    "root_cause": "Cached VPN credentials are stale.",
                    "reasoning": "KB0001 describes the same failure.",
                    "supporting_evidence": ["KB0001"],
                    "confidence": 0.9,
                })),
            ),
            patch(
                "src.agent.nodes.generate.get_llm",
                return_value=_build_static_llm(
                    "1. Reset the VPN credentials. [Source: KB0001]"
                ),
            ),
            patch(
                "src.agent.nodes.verify_evidence.get_llm",
                return_value=_build_static_llm(json.dumps({
                    "passed": True,
                    "feedback": "",
                    "invalid_steps": [],
                    "citation_findings": [],
                })),
            ),
        ])

    for active_patch in patches:
        active_patch.start()
    try:
        yield
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()


@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
def test_graph_routing_normal_risk(mock_search):
    """Normal risk incident should go through the full automated path to act."""
    with _mock_routing_agents(include_resolution_agents=True):
        graph = create_graph().compile()
        result = graph.invoke({
            "execution_id": "test_1",
            "incident_number": "INC_TEST_01",
            "incident_payload": {"description": "normal issue"},
        })

    assert result["action_taken"] == "resolved_automatically"
    assert result["risk"] == "low"  # Kept from incoming branch


@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_graph_routes_to_interrupt_when_no_evidence(mock_search):
    """Empty retrieval result -> human review, not diagnose."""
    with _mock_routing_agents():
        graph = create_graph().compile(checkpointer=MemorySaver())
        graph.invoke({
            "execution_id": "test_2",
            "incident_number": "INC_TEST_02",
            "incident_payload": {"description": "something unrelated"},
        }, config={"configurable": {"thread_id": "test_2"}})

    result = _paused(graph, "test_2")
    assert result["gate"] == "no_evidence"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "no_evidence"


@patch("src.agent.nodes.retrieve.search", side_effect=RuntimeError("qdrant down"))
def test_graph_routes_to_interrupt_when_retrieval_fails(mock_search):
    """Retrieval exception -> human review with retrieval_failed reason."""
    with _mock_routing_agents():
        graph = create_graph().compile(checkpointer=MemorySaver())
        graph.invoke({
            "execution_id": "test_3",
            "incident_number": "INC_TEST_03",
            "incident_payload": {"description": "vpn issue"},
        }, config={"configurable": {"thread_id": "test_3"}})

    result = _paused(graph, "test_3")
    assert result["gate"] == "retrieval_failed"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "retrieval_failed"


def test_graph_routing_high_risk():
    """High-risk incident should skip retrieval and go to interrupt."""
    with _mock_routing_agents(risk="high"):
        graph = create_graph().compile(checkpointer=MemorySaver())
        graph.invoke({
            "execution_id": "test_4",
            "incident_number": "INC_TEST_04",
            "incident_payload": {"description": "this is a high-risk task"},
        }, config={"configurable": {"thread_id": "test_4"}})

    # High risk pauses for human review, NOT act
    result = _paused(graph, "test_4")
    assert result["gate"] == "high_risk"
    assert result.get("action_taken") is None
    assert result["risk"] == "high"
    # Should NOT have retrieved evidence (skipped retrieval entirely)
    assert result.get("retrieved_evidence") is None


def test_compiled_graph_nodes_match_baseline():
    """
    The compiled graph must contain exactly the 11 baseline nodes + prepare_review (S3.4)
    + interrupt + __start__ + __end__.
    S3.1 must NOT add or remove any external node (e.g. no check_exhaustion
    or route_after_validate outside the internal multi-agent boundary).
    """
    expected_nodes = {
        "__start__",
        "__end__",
        "load",
        "validate",
        "classify",
        "determine_risk",
        "retrieve",
        "diagnose",
        "generate",
        "verify_evidence",
        "safety_check",
        "confidence_check",
        "prepare_review",
        "interrupt",
        "act",
    }
    compiled = create_graph().compile()
    actual_nodes = set(compiled.get_graph().nodes.keys())
    assert actual_nodes == expected_nodes, (
        f"Graph node set mismatch.\n"
        f"  Extra nodes: {actual_nodes - expected_nodes}\n"
        f"  Missing nodes: {expected_nodes - actual_nodes}"
    )


@patch("src.agent.nodes.retrieve.search")
@patch("src.agent.nodes.diagnose.get_llm")
@patch("src.agent.nodes.generate.get_llm")
@patch("src.agent.nodes.verify_evidence.get_llm")
def test_revised_draft_resolves_critic_flagged_issue(
    mock_critic_llm, mock_gen_llm, mock_diag_llm, mock_search
):
    """
    Explicitly verify that the Resolution Agent's *revised* draft resolves
    the exact issue the Critic flagged in its first review.

    Scenario:
      - First draft: step 1 has no citation -> Critic rejects.
      - Revised draft: step 1 includes [Source: KB0001] -> Critic passes.

    Assertions:
      - final resolution contains [Source: KB0001] (the missing citation is fixed).
      - revision_count == 1.
      - critic_verdict["passed"] is True.
      - action_taken == "resolved_automatically".
    """
    import json

    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "KB0001"
    chunk.text = "Procedure: restart the service to resolve login failures."
    chunk.score = 0.9
    mock_search.return_value = [chunk]

    diag_resp = MagicMock()
    diag_resp.content = json.dumps({
        "root_cause": "Service crashed.",
        "reasoning": "KB0001 confirms restart fixes this.",
        "supporting_evidence": ["KB0001"],
        "confidence": 0.85,
    })
    mock_diag_llm.return_value.invoke.return_value = diag_resp

    # Both drafts have [Source: KB0001] so structural check passes for both.
    # The Critic LLM is called for both â€” it fails the first (implausible action)
    # and passes the second (correct action, matching evidence).
    # This ensures the test exercises the LLM-driven revision path, not the
    # structural pre-check shortcut.
    bad_draft_resp = MagicMock()
    bad_draft_resp.content = "1. Restart the router. [Source: KB0001]"  # wrong action
    good_draft_resp = MagicMock()
    good_draft_resp.content = "1. Restart the service. [Source: KB0001]"  # correct action
    mock_gen_llm.return_value.invoke.side_effect = [bad_draft_resp, good_draft_resp]

    failing_verdict_resp = MagicMock()
    failing_verdict_resp.content = json.dumps({
        "passed": False,
        "feedback": "Step 1 says 'restart the router' but the evidence says restart the service.",
        "invalid_steps": [1],
        "citation_findings": [
            {"step_num": 1, "citation_id": "KB0001",
             "found_in_evidence": True, "plausible": False,
             "reason": "Action does not match evidence: should restart service, not router."},
        ],
    })
    passing_verdict_resp = MagicMock()
    passing_verdict_resp.content = json.dumps({
        "passed": True,
        "feedback": "",
        "invalid_steps": [],
        "citation_findings": [
            {"step_num": 1, "citation_id": "KB0001",
             "found_in_evidence": True, "plausible": True,
             "reason": "Action matches evidence: restart the service."},
        ],
    })
    mock_critic_llm.return_value.invoke.side_effect = [
        failing_verdict_resp,
        passing_verdict_resp,
    ]

    upstream = [
        patch("src.agent.nodes.validate.get_llm",
              return_value=_build_static_llm("valid")),
        patch("src.agent.nodes.classify.get_llm",
              return_value=_build_static_llm("network")),
        patch("src.agent.nodes.determine_risk.get_llm",
              return_value=_build_static_llm("low")),
    ]
    for p in upstream:
        p.start()
    try:
        graph = create_graph().compile()
        result = graph.invoke({
            "execution_id": "test-revision-content",
            "incident_number": "INC_REVISION_01",
            "incident_payload": {"description": "Users cannot log in to the service."},
        })
    finally:
        for p in upstream:
            p.stop()

    final_resolution = result["outputs"]["resolution"]
    assert "restart the service" in final_resolution.lower(), ( "Revised draft must correct the action flagged by the Critic (restart service, not router)." )
    assert "[Source: KB0001]" in final_resolution, (
        "Revised draft must include [Source: KB0001] â€” the citation missing in the first draft."
    )
    assert result.get("revision_count", 0) == 1
    assert result["critic_verdict"]["passed"] is True
    assert result["action_taken"] == "resolved_automatically"





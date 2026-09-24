import pytest
from unittest.mock import patch, MagicMock

from src.agent.graph import create_graph


def _fake_chunk():
    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "p1"
    chunk.text = "Reset the VPN credentials"
    chunk.score = 5.0
    return chunk


def _static_llm(content: str) -> MagicMock:
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = content
    mock_llm.invoke.return_value = mock_response
    return mock_llm


def _patch_llm_path(risk: str = "low", include_resolution_agents: bool = False):
    patches = [
        patch("src.agent.nodes.validate.get_llm", return_value=_static_llm("valid")),
        patch("src.agent.nodes.classify.get_llm", return_value=_static_llm("network")),
        patch("src.agent.nodes.determine_risk.get_llm", return_value=_static_llm(risk)),
    ]
    if include_resolution_agents:
        patches.extend([
            patch(
                "src.agent.nodes.diagnose.get_llm",
                return_value=_static_llm(
                    '{"root_cause":"VPN credential drift","reasoning":"KB0001 applies.",'
                    '"supporting_evidence":["KB0001"],"confidence":0.9}'
                ),
            ),
            patch(
                "src.agent.nodes.generate.get_llm",
                return_value=_static_llm("1. Reset the VPN credentials. [Source: KB0001]"),
            ),
            patch(
                "src.agent.nodes.verify_evidence.get_llm",
                return_value=_static_llm(
                    '{"passed":true,"feedback":"","invalid_steps":[],'
                    '"citation_findings":[{"step_num":1,"citation_id":"KB0001",'
                    '"found_in_evidence":true,"plausible":true,"reason":"Direct match."}]}'
                ),
            ),
        ])
    return patches


def _start_patches(patches):
    for p in patches:
        p.start()


def _stop_patches(patches):
    for p in reversed(patches):
        p.stop()


@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
def test_graph_routing_normal_risk(mock_search):
    """Normal risk incident should go through the full automated path to act."""
    patches = _patch_llm_path(include_resolution_agents=True)
    _start_patches(patches)
    try:
        graph = create_graph().compile()
        initial_state = {
            "execution_id": "test_1",
            "incident_number": "INC_TEST_01",
            "incident_payload": {"description": "normal issue"},
        }
        result = graph.invoke(initial_state)
    finally:
        _stop_patches(patches)

    assert result["action_taken"] == "resolved_automatically"
    assert result["risk"] == "low"  # Kept from incoming branch


@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_graph_routes_to_interrupt_when_no_evidence(mock_search):
    """Empty retrieval result -> human review, not diagnose."""
    patches = _patch_llm_path()
    _start_patches(patches)
    try:
        graph = create_graph().compile()
        result = graph.invoke({
            "execution_id": "test_2",
            "incident_number": "INC_TEST_02",
            "incident_payload": {"description": "something unrelated"},
        })
    finally:
        _stop_patches(patches)

    assert result["action_taken"] == "interrupted:no_evidence"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "no_evidence"


@patch("src.agent.nodes.retrieve.search")
def test_graph_routes_invalid_incident_to_interrupt_before_retrieval(mock_search):
    """Unrelated or invalid tickets must never produce an automated resolution."""
    patches = [
        patch("src.agent.nodes.validate.get_llm", return_value=_static_llm("invalid")),
        patch("src.agent.nodes.classify.get_llm", return_value=_static_llm("other")),
        patch("src.agent.nodes.determine_risk.get_llm", return_value=_static_llm("low")),
    ]
    _start_patches(patches)
    try:
        graph = create_graph().compile()
        result = graph.invoke({
            "execution_id": "test-invalid",
            "incident_number": "INC_TEST_INVALID",
            "incident_payload": {"description": "I need help choosing a restaurant."},
        })
    finally:
        _stop_patches(patches)

    mock_search.assert_not_called()
    assert result["outputs"]["eligibility"] == "invalid"
    assert result["action_taken"] == "interrupted:invalid_incident"
    assert result["human_review_required"] is True


@patch("src.agent.nodes.retrieve.search", side_effect=RuntimeError("qdrant down"))
def test_graph_routes_to_interrupt_when_retrieval_fails(mock_search):
    """Retrieval exception -> human review with retrieval_failed reason."""
    patches = _patch_llm_path()
    _start_patches(patches)
    try:
        graph = create_graph().compile()
        result = graph.invoke({
            "execution_id": "test_3",
            "incident_number": "INC_TEST_03",
            "incident_payload": {"description": "vpn issue"},
        })
    finally:
        _stop_patches(patches)

    assert result["action_taken"] == "interrupted:retrieval_failed"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "retrieval_failed"


def test_graph_routing_high_risk():
    """High-risk incident should skip retrieval and go to interrupt."""
    patches = _patch_llm_path(risk="high")
    _start_patches(patches)
    try:
        graph = create_graph().compile()
        initial_state = {
            "execution_id": "test_4",  # Updated to 4 to avoid conflict with test_2 above
            "incident_number": "INC_TEST_04",
            "incident_payload": {"description": "this is a high-risk task"}, # Standardized to "description"
        }
        result = graph.invoke(initial_state)
    finally:
        _stop_patches(patches)

    # High risk routes to interrupt, NOT act
    assert result["action_taken"] == "interrupted:high_risk_incident"
    assert result["risk"] == "high"
    # Should NOT have retrieved evidence (skipped retrieval entirely)
    assert result.get("retrieved_evidence") is None

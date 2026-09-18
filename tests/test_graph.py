import pytest
from src.agent.graph import create_graph


def test_graph_routing_normal_risk():
    """Normal risk incident should go through the full automated path to act."""
    graph = create_graph().compile()

    initial_state = {
        "execution_id": "test_1",
        "incident_number": "INC_TEST_01",
        "incident_payload": {"desc": "normal issue"},
    }

    result = graph.invoke(initial_state)

    assert result["action_taken"] == "resolved_automatically"
    assert result["risk"] == "low"


def test_graph_routing_high_risk():
    """High-risk incident should skip retrieval and go to interrupt."""
    graph = create_graph().compile()

    initial_state = {
        "execution_id": "test_2",
        "incident_number": "INC_TEST_02",
        "incident_payload": {"desc": "this is a high-risk task"},
    }

    result = graph.invoke(initial_state)

    # High risk routes to interrupt, NOT act
    assert result["action_taken"] == "interrupted:high_risk_incident"
    assert result["risk"] == "high"
    # Should NOT have retrieved evidence (skipped retrieval entirely)
    assert result.get("retrieved_evidence") is None

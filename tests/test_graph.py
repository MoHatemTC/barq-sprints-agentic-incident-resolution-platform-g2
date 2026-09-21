from unittest.mock import patch, MagicMock

from src.agent.graph import create_graph


def _fake_chunk():
    chunk = MagicMock()
    chunk.number = "KB0001"
    chunk.point_id = "p1"
    chunk.text = "Reset the VPN credentials"
    chunk.score = 5.0
    return chunk


@patch("src.agent.nodes.retrieve.search", return_value=[_fake_chunk()])
def test_graph_routing_normal_risk(mock_search):
    """Normal risk incident should go through the full automated path to act."""
    graph = create_graph().compile()
    initial_state = {
        "execution_id": "test_1",
        "incident_number": "INC_TEST_01",
        "incident_payload": {"description": "normal issue"},
    }

    result = graph.invoke(initial_state)

    assert result["action_taken"] == "resolved_automatically"


@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_graph_routes_to_interrupt_when_no_evidence(mock_search):
    """Empty retrieval result -> human review, not diagnose."""
    graph = create_graph().compile()
    result = graph.invoke({
        "execution_id": "test_2",
        "incident_number": "INC_TEST_02",
        "incident_payload": {"description": "something unrelated"},
    })

    assert result["action_taken"] == "interrupted:no_evidence"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "no_evidence"


@patch("src.agent.nodes.retrieve.search", side_effect=RuntimeError("qdrant down"))
def test_graph_routes_to_interrupt_when_retrieval_fails(mock_search):
    """Retrieval exception -> human review with retrieval_failed reason."""
    graph = create_graph().compile()
    result = graph.invoke({
        "execution_id": "test_3",
        "incident_number": "INC_TEST_03",
        "incident_payload": {"description": "vpn issue"},
    })

    assert result["action_taken"] == "interrupted:retrieval_failed"
    assert result["human_review_required"] is True
    assert result["failure_reason"] == "retrieval_failed"
from unittest.mock import MagicMock, patch

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
        "incident_payload": {
            "description": "normal issue"
        },
    }

    result = graph.invoke(initial_state)

    assert result["action_taken"] == "resolved_automatically"
    assert result["risk"] == "low"


@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_graph_routes_to_interrupt_when_no_evidence(mock_search):
    """Empty retrieval result should pause for human resolution."""

    graph = create_graph().compile()

    result = graph.invoke(
        {
            "execution_id": "test_2",
            "incident_number": "INC_TEST_02",
            "incident_payload": {
                "description": "something unrelated"
            },
        }
    )

    assert "__interrupt__" in result
    assert len(result["__interrupt__"]) == 1

    interrupt_value = result["__interrupt__"][0].value

    assert interrupt_value["type"] == "human_resolution_required"
    assert interrupt_value["execution_id"] == "test_2"
    assert interrupt_value["incident_number"] == "INC_TEST_02"
    assert interrupt_value["reason"] == "no_evidence"


@patch(
    "src.agent.nodes.retrieve.search",
    side_effect=RuntimeError("qdrant down"),
)
def test_graph_routes_to_interrupt_when_retrieval_fails(mock_search):
    """Retrieval exception should pause for human resolution."""

    graph = create_graph().compile()

    result = graph.invoke(
        {
            "execution_id": "test_3",
            "incident_number": "INC_TEST_03",
            "incident_payload": {
                "description": "vpn issue"
            },
        }
    )

    assert "__interrupt__" in result
    assert len(result["__interrupt__"]) == 1

    interrupt_value = result["__interrupt__"][0].value

    assert interrupt_value["type"] == "human_resolution_required"
    assert interrupt_value["execution_id"] == "test_3"
    assert interrupt_value["incident_number"] == "INC_TEST_03"
    assert interrupt_value["reason"] == "retrieval_failed"


def test_graph_routing_high_risk():
    """High-risk incident should skip retrieval and pause for human resolution."""

    graph = create_graph().compile()

    initial_state = {
        "execution_id": "test_4",
        "incident_number": "INC_TEST_04",
        "incident_payload": {
            "description": "this is a high-risk task"
        },
    }

    result = graph.invoke(initial_state)

    assert "__interrupt__" in result
    assert len(result["__interrupt__"]) == 1

    interrupt_value = result["__interrupt__"][0].value

    assert interrupt_value["type"] == "human_resolution_required"
    assert interrupt_value["execution_id"] == "test_4"
    assert interrupt_value["incident_number"] == "INC_TEST_04"
    assert interrupt_value["reason"] == "high_risk_incident"
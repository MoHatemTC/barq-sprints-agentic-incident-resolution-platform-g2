import pytest
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
    assert result["risk"] == "low"  # Kept from incoming branch


@patch("src.agent.nodes.retrieve.search", return_value=[])
def test_graph_routes_to_interrupt_when_no_evidence(mock_search):
    """Empty retrieval result -> human review, not diagnose."""
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
    graph = create_graph().compile(checkpointer=MemorySaver())

    initial_state = {
        "execution_id": "test_4",  # Updated to 4 to avoid conflict with test_2 above
        "incident_number": "INC_TEST_04",
        "incident_payload": {"description": "this is a high-risk task"}, # Standardized to "description"
    }

    graph.invoke(initial_state, config={"configurable": {"thread_id": "test_4"}})

    # High risk pauses for human review, NOT act
    result = _paused(graph, "test_4")
    assert result["gate"] == "high_risk"
    assert result.get("action_taken") is None
    assert result["risk"] == "high"
    # Should NOT have retrieved evidence (skipped retrieval entirely)
    assert result.get("retrieved_evidence") is None
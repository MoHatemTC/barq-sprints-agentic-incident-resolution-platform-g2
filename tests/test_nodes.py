import pytest
from unittest.mock import patch, MagicMock

from src.agent.nodes.load import load_node
from src.agent.nodes.determine_risk import determine_risk_node
from src.agent.nodes.retrieve import retrieve_node

@patch("src.agent.nodes.load.ServiceNowClient")
def test_load_node(mock_sn_client_class):
    mock_instance = mock_sn_client_class.return_value
    mock_instance.get_incident.return_value = {
        "sys_id": "abc123",
        "short_description": "Network down"
    }

    state = {
        "sys_id": "INC0001", 
        "short_description": "Network down"
    }

    state = {"incident_number": "INC0001"}
    result = load_node(state)
    assert result["incident_payload"]["sys_id"] == "INC0001"
    assert result["incident_payload"]["status"] == "loaded"
    assert result["incident_payload"]["short_description"] == "Network down"

def test_determine_risk_node_normal():
    state = {"incident_payload": {"description": "Server reboot requested."}}
    result = determine_risk_node(state)
    assert result["risk"] == "low"

def test_determine_risk_node_high():
    state = {"incident_payload": {"description": "high-risk data center wipe"}}
    result = determine_risk_node(state)
    assert result["risk"] == "high"

@patch("src.agent.nodes.retrieve.search")
def test_retrieve_node(mock_search):
    mock_chunk = MagicMock()
    mock_chunk.number = "KB123"
    mock_chunk.point_id = "KB123"
    mock_chunk.text = "Reboot the router"
    mock_chunk.score = 0.99

    mock_search.return_value = [mock_chunk]

    state = {"incident_payload": {"description": "router broken"}}
    result = retrieve_node(state)
    assert len(result["retrieved_evidence"]) > 0
    assert result["retrieved_evidence"][0]["id"] == "KB123"
    assert result["retrieved_evidence"][0]["score"] == 0.99
    assert result["retrieved_evidence"][0]["text"] == "Reboot the router"


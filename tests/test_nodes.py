import pytest
from unittest.mock import patch, MagicMock

from src.agent.nodes.load import load_node
from src.agent.nodes.determine_risk import determine_risk_node
from src.agent.nodes.retrieve import retrieve_node

@patch("src.agent.nodes.load.ServiceNowClient")
def test_load_node(mock_sn_client_class):
    mock_instance = mock_sn_client_class.return_value
    # 1. Setup the mock to return the enriched data from ServiceNow
    mock_instance.get_incident.return_value = {
        "sys_id": "abc123",  
        "short_description": "Network down",
        "priority": "1" # Adding an extra field to prove the merge worked
    }
    
    # 2. Add the sys_id to the incoming state payload
    state = {
        "incident_number": "INC0001",
        "incident_payload": {
            "sys_id": "abc123"
        }
    }
        
    result = load_node(state)
    
    # 3. Assertions
    assert result["incident_payload"]["sys_id"] == "abc123"
    assert result["incident_payload"]["short_description"] == "Network down"
    assert result["incident_payload"]["status"] == "loaded"

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

from src.agent.nodes.safety_check import safety_check_node

def test_safety_check_node_valid():
    state = {
        "outputs": {
            "resolution_text": "Restart the router.",
            "proposed_action": "update_incident"
        },
        "confidence": 0.95
    }
    result = safety_check_node(state)
    assert result["confidence"] == 0.95
    assert "action_taken" not in result

def test_safety_check_node_invalid_action():
    state = {
        "outputs": {
            "resolution_text": "Restart the router.",
            "proposed_action": "delete_incident"
        },
        "confidence": 0.95
    }
    result = safety_check_node(state)
    assert result["confidence"] == 0.0
    assert result["action_taken"] == "blocked_by_guardrail"
    assert "ACTION_BLOCKED" in result["failure_reason"]

def test_safety_check_node_invalid_content():
    state = {
        "outputs": {
            "resolution_text": "Use password=admin123 to login.",
            "proposed_action": "update_incident"
        },
        "confidence": 0.95
    }
    result = safety_check_node(state)
    assert result["confidence"] == 0.0
    assert result["action_taken"] == "blocked_by_guardrail"
    assert "OUTPUT_CONTENT_FLAGGED" in result["failure_reason"]

import pytest
from src.agent.nodes.load import load_node
from src.agent.nodes.determine_risk import determine_risk_node
from src.agent.nodes.retrieve import retrieve_node

def test_load_node():
    state = {"incident_number": "INC0001"}
    result = load_node(state)
    assert result["incident_payload"]["sys_id"] == "INC0001"
    assert result["incident_payload"]["status"] == "loaded"

def test_determine_risk_node_normal():
    state = {"incident_payload": {"description": "Server reboot requested."}}
    result = determine_risk_node(state)
    assert result["risk"] == "normal"

def test_determine_risk_node_high():
    state = {"incident_payload": {"description": "high-risk data center wipe"}}
    result = determine_risk_node(state)
    assert result["risk"] == "high"

def test_retrieve_node():
    state = {}
    result = retrieve_node(state)
    assert len(result["retrieved_evidence"]) > 0
    assert result["retrieved_evidence"][0]["id"] == "KB123"

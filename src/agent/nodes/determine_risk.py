from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="determine_risk")
def determine_risk_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute before retrieval, persist risk determination.
    Routes high-risk incidents to human path.
    """
    # Mocking risk determination
    # If the payload description has 'high-risk', make it high risk
    payload = state.get("incident_payload", {})
    risk = "normal"
    if payload and "high-risk" in str(payload):
        risk = "high"
    return {"risk": risk}

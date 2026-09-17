from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="classify")
def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Determine incident classification."""
    return {"classification": "network_issue"}

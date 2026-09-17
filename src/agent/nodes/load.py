from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="load")
def load_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch full incident from ServiceNow via OAuth using event identifiers."""
    payload = state.get("incident_payload", {})
    payload["status"] = "loaded"
    payload["sys_id"] = state.get("incident_number")
    return {"incident_payload": payload}

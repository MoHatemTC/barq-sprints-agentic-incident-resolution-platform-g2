from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="act")
def act_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final act node or interrupt for human approval."""
    action = "resolved_automatically"
    if state.get("risk") == "high":
        action = "routed_to_human"
    return {"action_taken": action}

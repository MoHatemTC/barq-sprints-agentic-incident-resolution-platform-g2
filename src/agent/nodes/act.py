from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="act")
def act_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final act node: automated path, or the outcome of a human decision. """
    decision = state.get("human_decision")
    if decision is None:
        return {"action_taken": "resolved_automatically"}
    if decision.get("decision") == "approve":
        return {"action_taken": "approved_by_human"}
    return {"action_taken": "rejected_by_human"}

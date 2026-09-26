from typing import Dict, Any
from langgraph.types import interrupt
from src.observability.tracing import trace_node


def normalize_decision(decision: Any) -> Dict[str, Any]:
    """Coerce the resume value into a decision dict"""
    if not isinstance(decision, dict):
        decision = {}
    return {
        "decision": "approve" if decision.get("decision") == "approve" else "reject",
        "reviewer": decision.get("reviewer"),
        "comment": decision.get("comment"),
    }


@trace_node(name="interrupt")
def interrupt_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pause the graph until a human decision is persisted"""
    decision = interrupt(state.get("interrupt_payload") or {})
    return {"human_decision": normalize_decision(decision)}

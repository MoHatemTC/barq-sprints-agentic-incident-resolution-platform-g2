from typing import Any, Dict, Optional
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


def extract_human_solution(decision: Any, normalized: Dict[str, Any]) -> Optional[str]:
    """S3.5: the reviewer's written resolution, kept only on an approve."""
    if normalized["decision"] != "approve" or not isinstance(decision, dict):
        return None
    solution = decision.get("human_solution")
    if not isinstance(solution, str) or not solution.strip():
        return None
    return solution.strip()


@trace_node(name="interrupt")
def interrupt_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pause the graph until a human decision is persisted"""
    decision = interrupt(state.get("interrupt_payload") or {})
    normalized = normalize_decision(decision)
    return {
        "human_decision": normalized,
        "human_solution": extract_human_solution(decision, normalized),
    }

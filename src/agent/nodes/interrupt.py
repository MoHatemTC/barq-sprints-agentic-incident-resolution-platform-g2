from typing import Dict, Any
from src.observability.tracing import trace_node


@trace_node(name="interrupt")
def interrupt_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pause execution and present the incident to a human for review.

    The graph checkpoints state here so a human can inspect the incident,
    the retrieved evidence, the draft, and the guardrail verdicts, then
    resume once a decision is persisted.
    """
    risk = state.get("risk", "normal")
    confidence = state.get("confidence")

    if state.get("action_taken") == "blocked_by_guardrail":
        return {
            "action_taken": state.get("action_taken"),
            "human_review_required": True,
            "failure_reason": state.get("failure_reason"),
        }

    if risk == "high":
        reason = "high_risk_incident"
    elif state.get("retrieval_failed"):
        reason = "retrieval_failed"
    elif not state.get("retrieved_evidence"):
        reason = "no_evidence"
    elif confidence is not None and confidence < 0.7:
        reason = "low_confidence"
    else:
        reason = "manual_review_requested"

    return {
        "action_taken": f"interrupted:{reason}",
        "human_review_required": True,
        "failure_reason": reason,
    }


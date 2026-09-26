from typing import Dict, Any

from langgraph.types import interrupt

from src.observability.tracing import trace_node


@trace_node(name="interrupt")
def interrupt_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pause the graph and wait for a human resolution.

    LangGraph checkpoints the state before returning control
    to the caller. The graph can later resume using the same
    thread_id.
    """

    risk = state.get("risk", "normal")
    confidence = state.get("confidence")

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

    human_input = interrupt(
        {
            "type": "human_resolution_required",
            "execution_id": state.get("execution_id"),
            "incident_number": state.get("incident_number"),
            "reason": reason,
            "message": "Human resolution is required before continuing.",
        }
    )

    # Command(resume=...) sends the complete decision payload.
    # Extract only the actual human-authored resolution.
    if isinstance(human_input, dict):
        human_solution = human_input.get("human_solution")

        if not human_solution:
            human_solution = human_input.get("comment")

        decision = human_input.get("decision")
    else:
        human_solution = str(human_input) if human_input else None
        decision = "approve"

    return {
        "action_taken": "human_resolution_received",
        "human_review_required": False,
        "failure_reason": reason,
        "human_solution": human_solution,
        "human_decision": decision,
    }
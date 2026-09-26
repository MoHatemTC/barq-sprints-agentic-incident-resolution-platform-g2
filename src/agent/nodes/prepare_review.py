from datetime import datetime, timezone
from typing import Dict, Any
from src.observability.tracing import trace_node
from src.agent.approval_brief import generate_approval_brief

# Plain-language reason per gate, shown to the reviewer next to the raw data.
GATE_REASONS = {
    "invalid_incident": "Validation found the ticket invalid or out of scope for the agent",
    "critic_exhausted": "The critic rejected the draft and the revision budget ran out",
    "safety_blocked": "The safety check blocked the proposed action",
    "high_risk": "The incident was classified as high risk",
    "retrieval_failed": "Knowledge base retrieval failed",
    "no_evidence": "No knowledge base evidence was found for this incident",
    "low_confidence": "Confidence in the proposed resolution is below the floor",
}


def detect_gate(state: Dict[str, Any]) -> str:
    # route_after_validate: invalid ticket goes to review before classify runs
    if (state.get("outputs") or {}).get("eligibility") == "invalid":
        return "invalid_incident"
    # route_after_risk: high risk goes to review before retrieve runs
    if state.get("risk") == "high":
        return "high_risk"
    # route_after_retrieve: missing evidence
    if not state.get("retrieved_evidence"):
        return "retrieval_failed" if state.get("retrieval_failed") else "no_evidence"
    # route_after_critic: revision budget ran out
    if state.get("critic_exhausted"):
        return "critic_exhausted"
    # route_after_confidence: guardrail block, else low confidence
    if state.get("action_taken") == "blocked_by_guardrail":
        return "safety_blocked"
    return "low_confidence"


@trace_node(name="prepare_review")
def prepare_review_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build the payload a human reviews before the graph pauses"""
    gate = detect_gate(state)
    outputs = state.get("outputs") or {}

    payload = {
        "gate": gate,
        "reason_text": GATE_REASONS[gate],
        "incident": state.get("incident_payload") or {},
        "evidence": state.get("retrieved_evidence") or [],
        "draft": {
            "diagnosis": outputs.get("diagnosis"),
            "resolution": outputs.get("resolution"),
        },
        "verdicts": {
            "risk": state.get("risk"),
            "confidence": state.get("confidence"),
            "critic_verdict": state.get("critic_verdict"),
            "guardrail": state.get("failure_reason")
            if state.get("action_taken") == "blocked_by_guardrail"
            else None,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Built here, not in interrupt, so the LLM runs once; stored apart from the raw payload
    brief = generate_approval_brief(payload)

    return {
        "gate": gate,
        "interrupt_payload": payload,
        "approval_brief": brief,
        "human_review_required": True,
        "failure_reason": gate,
    }

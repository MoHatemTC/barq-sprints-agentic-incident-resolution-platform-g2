from datetime import datetime, timezone
from typing import Dict, Any
from src.observability.tracing import trace_node

# Plain-language reason per gate, shown to the reviewer next to the raw data.
GATE_REASONS = {
    "critic_exhausted": "The critic rejected the draft and the revision budget ran out",
    "safety_blocked": "The safety check blocked the proposed action",
    "high_risk": "The incident was classified as high risk",
    "retrieval_failed": "Knowledge base retrieval failed",
    "no_evidence": "No knowledge base evidence was found for this incident",
    "low_confidence": "Confidence in the proposed resolution is below the floor",
}


def detect_gate(state: Dict[str, Any]) -> str:
    """Return the gate that sent this run to human review"""
    if state.get("critic_exhausted"):
        return "critic_exhausted"
    if state.get("action_taken") == "blocked_by_guardrail":
        return "safety_blocked"
    if state.get("risk") == "high":
        return "high_risk"
    if state.get("retrieval_failed"):
        return "retrieval_failed"
    if not state.get("retrieved_evidence"):
        return "no_evidence"
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

    return {
        "gate": gate,
        "interrupt_payload": payload,
        "human_review_required": True,
        "failure_reason": gate,
    }

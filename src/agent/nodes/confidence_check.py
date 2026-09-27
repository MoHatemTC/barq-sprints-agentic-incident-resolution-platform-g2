from typing import Dict, Any
import logging

from src.observability.tracing import trace_node

logger = logging.getLogger(__name__)


@trace_node(name="confidence_check")
def confidence_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Read the real confidence score produced by the Diagnostic Agent.

    Reads outputs["diagnosis_structured"]["confidence"] which is set by
    diagnose_node using a blend of LLM self-reported confidence and a
    heuristic based on retrieved-evidence quality.

    Falls back to 0.0 when the diagnose node did not run (e.g. early
    HITL escalation paths), so those tickets are correctly routed to
    human review rather than auto-acted upon.
    """
    outputs = state.get("outputs") or {}
    diagnosis_structured = outputs.get("diagnosis_structured") or {}
    confidence = diagnosis_structured.get("confidence")

    if confidence is None:
        # diagnose_node did not run (early escalation / invalid ticket).
        confidence = 0.0
        logger.info("confidence_check: no diagnosis confidence found, defaulting to 0.0")
    else:
        confidence = float(confidence)
        logger.info(f"confidence_check: real confidence={confidence:.4f}")

    return {"confidence": confidence}

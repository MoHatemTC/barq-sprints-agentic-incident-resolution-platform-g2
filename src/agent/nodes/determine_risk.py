import json
from typing import Dict, Any
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

# ServiceNow priority → risk anchor.
# P1 (Critical) and P2 (High) are always escalated unless the LLM explicitly
# disagrees AND the description contains no critical-system-down indicators.
# P3–P5 default to low unless the description reveals an unmistakably critical
# situation (e.g. production data breach, complete business outage).
_SN_PRIORITY_ANCHOR: Dict[str, str] = {
    "1": "high",  # Critical
    "2": "high",  # High
    "3": "low",   # Moderate
    "4": "low",   # Low
    "5": "low",   # Planning
}


@trace_node(name="determine_risk", observation_type="generation")
def determine_risk_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute before retrieval, persist risk determination.
    Routes high-risk incidents to human path.

    Risk is anchored to the ServiceNow priority field first:
      P1/P2  → high  (LLM may confirm or escalate, never downgrade)
      P3–P5  → low   (LLM may escalate to high only for unmistakable
                       critical-system-down / data-breach scenarios)

    This prevents false high-risk classifications caused by alarming
    keywords (e.g. "FATAL", "connection refused") in routine low-priority
    incidents.
    """
    payload = state.get("incident_payload", {})

    decision = state.get("human_decision") or {}
    if decision.get("decision") == "approve" and state.get("risk"):
        return {}  # keep the risk the reviewer approved

    # --- Priority anchor ---
    sn_priority = str(payload.get("priority", "")).strip()

    if sn_priority in ("1", "2"):
        # P1 / P2 from ServiceNow are critical/high by definition (model is not called)
        risk = "high"
    elif sn_priority in ("3", "4", "5"):
        llm = get_llm()
        # P3–P5: routine/low priority by default unless catastrophic
        prompt = f"""
    You are an expert IT triage agent.
    Review the following incident payload and determine if it is "high" risk or "low" risk.
    This incident has ServiceNow Priority P{sn_priority} (non-critical).
    High risk incidents involve critical production systems completely down, data breaches, or catastrophic business impact.
    Routine issues, non-critical bugs, or secondary/replica systems are low risk.

    Incident Payload:
    {json.dumps(payload, indent=2)}

    Respond with ONLY the word "high" or "low".
    """
        response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})
        content = response.content if hasattr(response, "content") else str(response)
        risk = "high" if "high" in content.strip().lower() else "low"
    else:
        llm = get_llm()
        # Standard canonical prompt when priority is not specified (e.g. test fixtures)
        prompt = f"""
    You are an expert IT triage agent.
    Review the following incident payload and determine if it is "high" risk or "low" risk.
    High risk incidents involve critical systems down, data breaches, or significant business impact.
    Low risk incidents are routine issues, password resets, or non-critical bugs.

    Incident Payload:
    {json.dumps(payload, indent=2)}

    Respond with ONLY the word "high" or "low".
    """
        response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})
        content = response.content if hasattr(response, "content") else str(response)
        risk = "high" if "high" in content.strip().lower() else "low"

    return {"risk": risk}

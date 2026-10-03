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
    priority_anchor = _SN_PRIORITY_ANCHOR.get(sn_priority)

    # Map numeric priority to human-readable label for the prompt.
    priority_labels = {
        "1": "1 - Critical",
        "2": "2 - High",
        "3": "3 - Moderate",
        "4": "4 - Low",
        "5": "5 - Planning",
    }
    priority_label = priority_labels.get(sn_priority, f"unknown ({sn_priority})")

    llm = get_llm()

    if priority_anchor == "high":
        # P1/P2: default high, LLM cannot downgrade.
        prompt = f"""
    You are an expert IT triage agent.
    This incident has ServiceNow Priority {priority_label}, which is inherently HIGH risk.
    Confirm whether it is "high" risk.
    Only answer "low" if the incident is clearly a false alarm, test ticket, or spam —
    not because the scope seems limited.

    Incident Payload:
    {json.dumps(payload, indent=2)}

    Respond with ONLY the word "high" or "low".
    """
    else:
        # P3–P5: default low, LLM can escalate only for unmistakable critical scenarios.
        prompt = f"""
    You are an expert IT triage agent.
    This incident has ServiceNow Priority {priority_label}, which is LOW risk by default.
    Determine if it is "high" or "low" risk.

    Answer "high" ONLY if the description clearly indicates ALL of the following:
      - A production system (not replica, not analytics, not dev/test) is completely down, OR
      - There is an active data breach or data loss, OR
      - The incident has direct, immediate business-critical financial impact.

    If any doubt exists, or the incident affects only a secondary/replica/analytics system,
    answer "low".

    Incident Payload:
    {json.dumps(payload, indent=2)}

    Respond with ONLY the word "high" or "low".
    """

    response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})
    content = response.content if hasattr(response, "content") else str(response)

    risk = content.strip().lower()
    if "high" in risk:
        risk = "high"
    else:
        risk = "low"

    # Safety net: P1/P2 can never be downgraded to low by the LLM alone.
    if priority_anchor == "high":
        risk = "high"

    return {"risk": risk}

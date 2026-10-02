import re
from typing import Dict, Any

from src.config import INCIDENT_CATEGORIES
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

# The same categories ServiceNow's incident form offers,
CATEGORY_HINTS = {
    "inquiry": "questions, how-to, process or policy, account lockout and access requests",
    "software": "applications, email, SAP, services returning errors, crashes after an update",
    "hardware": "laptops, desktops, printers, monitors, peripherals, physical faults",
    "network": "VPN, Wi-Fi, connectivity, mapped drives, DNS, firewalls",
    "database": "database outages, slow queries, connection pools, data errors",
    "password_reset": "forgotten or expired passwords, MFA re-enrolment",
}


@trace_node(name="classify", observation_type="generation")
def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Determine incident classification using LLM based on description."""
    payload = state.get("incident_payload", {})

    desc = payload.get("description", "")
    short_desc = payload.get("short_description", "")
    full_text = f"Title: {short_desc}\nDescription: {desc}".strip()

    if not full_text:
        return {"classification": "unknown"}

    categories = "\n".join(f"    - {c}: {CATEGORY_HINTS[c]}" for c in INCIDENT_CATEGORIES)
    prompt = f"""
    You are an IT incident classifier.
    Read the following incident and classify it into ONE of these ServiceNow categories:
{categories}

    Incident Description:
    {full_text}

    Respond with ONLY the exact category name from the list above. Do not add any extra text.
    """

    response = get_llm().invoke(prompt, config={"callbacks": get_llm_callback()})
    content = response.content if hasattr(response, "content") else str(response)

    words = re.findall(r"[a-z_]+", content.lower())
    final_class = next((w for w in words if w in INCIDENT_CATEGORIES), "inquiry")
    return {"classification": final_class}

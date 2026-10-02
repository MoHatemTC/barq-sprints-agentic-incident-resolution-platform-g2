import logging
import re
from typing import Dict, Any

from src.config import INCIDENT_CATEGORIES
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

logger = logging.getLogger(__name__)

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
    """Determine incident classification using LLM based on description.

    When a ``human_solution`` is present in the state (i.e. after an
    approval with reviewer feedback), the reviewer's comment is included
    in the prompt so the model can re-evaluate the incident category.
    """
    payload = state.get("incident_payload", {})
    human_solution = state.get("human_solution", "")

    desc = payload.get("description", "")
    short_desc = payload.get("short_description", "")
    full_text = f"Title: {short_desc}\nDescription: {desc}".strip()

    if not full_text:
        return {"classification": "unknown"}

    # When a human reviewer has provided feedback, inject it into the
    # prompt so the LLM can reconsider the category.
    human_feedback_block = ""
    if human_solution:
        logger.info(
            "Re-classifying with human reviewer feedback (%d chars)",
            len(human_solution),
        )
        human_feedback_block = f"""

    Human Reviewer Feedback / Resolution:
    {human_solution}

    The reviewer's feedback may reveal the true nature of the incident.
    Consider it carefully when determining the category."""

    categories = "\n".join(f"    - {c}: {CATEGORY_HINTS[c]}" for c in INCIDENT_CATEGORIES)
    prompt = f"""
    You are an IT incident classifier.
    Read the following incident and classify it into ONE of these ServiceNow categories:
{categories}

    Incident Description:
    {full_text}
{human_feedback_block}

    Respond with ONLY the exact category name from the list above. Do not add any extra text.
    """

    response = get_llm().invoke(prompt, config={"callbacks": get_llm_callback()})
    content = response.content if hasattr(response, "content") else str(response)

    words = re.findall(r"[a-z_]+", content.lower())
    final_class = next((w for w in words if w in INCIDENT_CATEGORIES), "inquiry")

    if human_solution:
        logger.info(
            "Re-classification result with human feedback: %s",
            final_class,
        )

    return {"classification": final_class}

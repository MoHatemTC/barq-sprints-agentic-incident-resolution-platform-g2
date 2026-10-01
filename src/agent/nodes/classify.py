import logging
from typing import Dict, Any
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

logger = logging.getLogger(__name__)

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

    llm = get_llm()
    prompt = f"""
    You are an IT incident classifier.
    Read the following incident description and classify it into ONE of the following categories:
    - network
    - database
    - software
    - hardware
    - access
    - security
    - email
    - cloud
    - storage
    - other

    Incident Description:
    {full_text}
{human_feedback_block}

    Respond with ONLY the exact category name from the list above. Do not add any extra text.
    """

    response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})

    content = response.content if hasattr(response, "content") else str(response)

    classification = content.strip().lower()

    valid_categories = [
        "network", "database", "software", "hardware", "access",
        "security", "email", "cloud", "storage", "other"
    ]
    final_class = "other"
    for cat in valid_categories:
        if cat in classification:
            final_class = cat
            break

    if human_solution:
        logger.info(
            "Re-classification result with human feedback: %s",
            final_class,
        )

    return {"classification": final_class}

import json
from typing import Dict, Any
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

@trace_node(name="validate", observation_type="generation")
def validate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Re-check incident eligibility at execution time using LLM."""
    payload = state.get("incident_payload", {})
    
    desc = str(payload.get("description") or "").strip()
    short_desc = str(payload.get("short_description") or "").strip()
    
    outputs = state.get("outputs", {})

    if not desc and not short_desc:
        outputs["eligibility"] = "invalid"
        outputs["validation_reason"] = "Empty description"
        return {"outputs": outputs}

    full_text = f"Title: {short_desc}\nDescription: {desc}".strip()

    llm = get_llm()
    prompt = f"""
    You are an IT triage validation agent.
    Review the following incident description and determine if it is "valid" or "invalid".
    - "valid": It is a real IT issue or request with enough context to understand the problem.
    - "invalid": It is gibberish, a test ticket, completely empty, or unrelated to IT.

    Incident:
    {full_text}

    Respond with ONLY the word "valid" or "invalid".
    """

    response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})

    content = response.content if hasattr(response, "content") else str(response)
    
    eligibility = content.strip().lower()
    if "invalid" in eligibility:
        eligibility = "invalid"
    else:
        eligibility = "valid"
        
    outputs["eligibility"] = eligibility
    return {"outputs": outputs}

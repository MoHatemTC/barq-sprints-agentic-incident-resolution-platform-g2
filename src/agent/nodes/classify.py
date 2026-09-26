from typing import Dict, Any
from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm

@trace_node(name="classify", observation_type="generation")
def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Determine incident classification using LLM based on description."""
    payload = state.get("incident_payload", {})
    
    desc = payload.get("description", "")
    short_desc = payload.get("short_description", "")
    full_text = f"Title: {short_desc}\nDescription: {desc}".strip()
    
    if not full_text:
        return {"classification": "unknown"}

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
            
    return {"classification": final_class}

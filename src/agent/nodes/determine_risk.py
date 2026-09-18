import json
from typing import Dict, Any
from src.observability.tracing import trace_node
from src.agent.llm import get_llm

@trace_node(name="determine_risk", observation_type="generation")
def determine_risk_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute before retrieval, persist risk determination.
    Routes high-risk incidents to human path.
    """
    
    payload = state.get("incident_payload", {})
    
    llm = get_llm()
    prompt = f"""
    You are an expert IT triage agent.
    Review the following incident payload and determine if it is "high" risk or "low" risk.
    High risk incidents involve critical systems down, data breaches, or significant business impact.
    Low risk incidents are routine issues, password resets, or non-critical bugs.

    Incident Payload:
    {json.dumps(payload, indent=2)}

    Respond with ONLY the word "high" or "low".
    """
    
    # Get the LangChain handler linked to the current trace context
    try:
        import importlib

        langfuse_context = importlib.import_module(
            "langfuse.decorators"
        ).langfuse_context
        handler = langfuse_context.get_current_langchain_handler()
        config = {"callbacks": [handler]}
    except (ImportError, AttributeError):
        config = {}

    response = llm.invoke(prompt, config=config)
    content = response.content if hasattr(response, "content") else str(response)
    
    risk = content.strip().lower()
    if "high" in risk:
        risk = "high"
    else:
        risk = "low"
        
    return {"risk": risk}

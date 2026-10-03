import json
import logging
from typing import Dict, Any

from src.agent.llm import get_llm
from src.agent.prompts import QUERY_GENERATION_PROMPT
from src.observability.tracing import trace_node

logger = logging.getLogger(__name__)

@trace_node(name="formulate_query")
def formulate_query_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formulates an optimized search query using an LLM based on the
    incident description and short description.
    """
    payload = state.get("incident_payload", {})
    short_description = payload.get("short_description", "")
    description = payload.get("description", "")
    human_solution = state.get("human_solution", "")
    
    # If there's no meaningful text to search with, fallback safely
    if not str(short_description).strip() and not str(description).strip():
        logger.warning("Empty incident description. Skipping query formulation.")
        return {"search_query": ""}

    try:
        llm = get_llm()
        prompt = QUERY_GENERATION_PROMPT.format(
            short_description=short_description,
            description=description,
            human_solution=human_solution
        )
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        
        # Clean up JSON if wrapped in markdown
        cleaned = content.strip()
        if cleaned.startswith("```"):
            import re
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
            
        result = json.loads(cleaned)
        search_query = result.get("query", "")
        
        if not search_query:
            # Fallback to direct concatenation if LLM returns empty
            search_query = f"{short_description}\n{description}"
                
        logger.info(f"Formulated search query: {search_query[:100]}...")
        return {"search_query": search_query}
        
    except Exception as e:
        logger.exception(f"Query formulation failed: {e}. Falling back to default text.")
        # Fallback in case of JSON parse error or LLM failure
        fallback_query = f"{short_description}\n{description}"
        return {"search_query": fallback_query}

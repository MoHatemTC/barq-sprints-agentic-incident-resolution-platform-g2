import json
import logging
import re
from typing import Dict, Any

from src.agent.llm import get_llm
from src.agent.prompts import QUERY_GENERATION_PROMPT
from src.observability.tracing import trace_node, get_llm_callback

logger = logging.getLogger(__name__)

@trace_node(name="formulate_query")
def formulate_query_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formulates an optimised, concise KB search query from the incident
    description using an LLM.

    Reads:
      state["incident_payload"]["short_description"] — incident title
      state["incident_payload"]["description"]       — incident detail
      state["human_solution"]                        — reviewer hint (optional)

    Writes:
      state["search_query"] — a single focused search string for retrieve_node
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
            human_solution=human_solution,
        )
        # get_llm_callback() nests the LLM generation under this node's span
        response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})
        content = response.content if hasattr(response, "content") else str(response)

        # Clean up JSON if wrapped in markdown code fences
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        result = json.loads(cleaned)
        search_query = result.get("query", "")

        if not search_query:
            # Fallback to direct concatenation if LLM returns empty
            search_query = f"{short_description}\n{description}".strip()
            if human_solution:
                search_query += f"\nHuman-provided resolution:\n{human_solution}"

        logger.info("Formulated search query: %s…", search_query[:100])
        return {"search_query": search_query}

    except Exception as e:
        logger.exception("Query formulation failed: %s. Falling back to raw text.", e)
        fallback_query = f"{short_description}\n{description}".strip()
        if human_solution:
            fallback_query += f"\nHuman-provided resolution:\n{human_solution}"
        return {"search_query": fallback_query}


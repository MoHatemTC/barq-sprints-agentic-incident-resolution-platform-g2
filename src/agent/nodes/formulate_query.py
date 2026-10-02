import json
import logging
from typing import Dict, Any

from src.agent.llm import get_llm
from src.agent.prompts import QUERY_GENERATION_PROMPT
from src.observability.tracing import trace_node, get_llm_callback

logger = logging.getLogger(__name__)


def _parse_query(content: str) -> str:
    """The query from the LLM's answer: one line, or {"query": ...} if it answers in JSON."""
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get("query", "")).strip()
    except ValueError:
        pass
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0].strip('"') if lines else ""


@trace_node(name="formulate_query")
def formulate_query_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Writes the KB search query from the short description and the description combined.

    Reads:
      state["incident_payload"]["short_description"] — incident title
      state["incident_payload"]["description"]       — incident detail
      state["human_solution"]                        — reviewer hint (optional)

    Writes:
      state["search_query"] — a single focused search string for retrieve_node.
      Falls back to both texts as they are if the LLM fails or answers off-format.
    """
    payload = state.get("incident_payload", {})
    short_description = str(payload.get("short_description") or "").strip()
    description = str(payload.get("description") or "").strip()
    human_solution = state.get("human_solution", "")

    raw = "\n".join(dict.fromkeys(t for t in (short_description, description) if t))
    if not raw:
        logger.warning("Empty incident description. Skipping query formulation.")
        return {"search_query": ""}
    fallback = f"{raw}\nHuman-provided resolution:\n{human_solution}" if human_solution else raw

    try:
        prompt = QUERY_GENERATION_PROMPT.format(
            short_description=short_description,
            description=description,
            human_solution=human_solution,
        )
        # get_llm_callback() nests the LLM generation under this node's span
        response = get_llm().invoke(prompt, config={"callbacks": get_llm_callback()})
        content = response.content if hasattr(response, "content") else str(response)
        search_query = _parse_query(content)
    except Exception as e:
        logger.exception("Query formulation failed: %s. Falling back to raw text.", e)
        return {"search_query": fallback}

    if not 3 <= len(search_query) <= 300:
        search_query = fallback
    logger.info("Formulated search query: %s…", search_query[:100])
    return {"search_query": search_query}

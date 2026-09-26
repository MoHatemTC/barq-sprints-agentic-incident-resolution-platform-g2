import json
import logging
from typing import Any, Dict

from src.agent.llm import get_llm
from src.agent.prompts import ARTICLE_COMPOSER_PROMPT

logger = logging.getLogger(__name__)


def compose_article(
    incident_snapshot: Dict[str, Any],
    human_solution: str,
) -> Dict[str, Any]:
    """
    Compose a structured knowledge-base article from an incident
    snapshot and a human-provided resolution.
    """

    if not human_solution or not human_solution.strip():
        raise ValueError("human_solution is required")

    prompt = ARTICLE_COMPOSER_PROMPT.format(
        incident_snapshot=json.dumps(
            incident_snapshot,
            ensure_ascii=False,
            indent=2,
        ),
        human_solution=human_solution.strip(),
    )

    llm = get_llm()
    response = llm.invoke(prompt)

    content = response.content if hasattr(response, "content") else str(response)

    try:
        article = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("Article Composer returned invalid JSON: %s", content)
        raise ValueError("Article Composer returned invalid JSON") from exc

    _validate_article(article)

    return article


def _validate_article(article: Dict[str, Any]) -> None:
    """Validate the minimum structure required for a KB article."""

    if not isinstance(article, dict):
        raise ValueError("Article must be a JSON object")

    if not isinstance(article.get("title"), str) or not article["title"].strip():
        raise ValueError("Article title is required")

    if not isinstance(article.get("summary"), str):
        raise ValueError("Article summary is required")

    steps = article.get("steps")

    if not isinstance(steps, list) or not steps:
        raise ValueError("Article must contain at least one step")

    if not all(isinstance(step, str) and step.strip() for step in steps):
        raise ValueError("Every article step must be a non-empty string")
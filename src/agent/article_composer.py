import json
import logging
import re
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
        article = json.loads(_json_object_text(content))
    except json.JSONDecodeError as exc:
        logger.error("Article Composer returned invalid JSON: %s", content)
        raise ValueError("Article Composer returned invalid JSON") from exc

    _validate_article(article)
    _validate_faithfulness(
        article,
        incident_snapshot,
        human_solution,
    )

    return article


def _json_object_text(content: str) -> str:
    """The JSON object in an LLM reply; real models often wrap it in ```json fences."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return content
    return content[start:end + 1]


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


def _validate_faithfulness(
    article: Dict[str, Any],
    incident_snapshot: Dict[str, Any],
    human_solution: str,
) -> None:
    """Reject clearly unsupported technical details in generated content."""

    source_text = " ".join(
        str(value)
        for value in incident_snapshot.values()
    )
    source_text = f"{source_text} {human_solution}".lower()

    generated_text = " ".join(
        [
            article["title"],
            article["summary"],
            *article["steps"],
        ]
    ).lower()

    technical_terms = {
        "database",
        "server",
        "configuration",
        "command",
        "sql",
        "firewall",
        "endpoint",
        "credential",
        "password",
        "port",
        "hostname",
        "production",
    }

    # Whole words only: a substring test flags "reported" as "port" and "mysql" as "sql"
    def mentions(text: str, term: str) -> bool:
        return re.search(rf"\b{re.escape(term)}\b", text) is not None

    unsupported = {
        term
        for term in technical_terms
        if mentions(generated_text, term) and not mentions(source_text, term)
    }

    if unsupported:
        raise ValueError(
            "Article contains unsupported technical details: "
            + ", ".join(sorted(unsupported))
        )

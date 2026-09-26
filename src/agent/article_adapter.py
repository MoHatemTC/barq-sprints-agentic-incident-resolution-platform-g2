from typing import Any, Dict

from src.retrieval.schema import Article


HUMAN_RESOLUTION_SECTION = "Human Resolution"


def composer_result_to_article(
    composed: Dict[str, Any],
    article_number: str,
    category: str,
    service: str = "general",
    security_level: str = "internal",
) -> Article:
    """
    Convert an Article Composer result into the canonical Article model.

    Human-resolution articles remain published for retrieval, but use
    security_level="human_resolution" to distinguish operator-derived
    knowledge from the curated corpus.
    """

    title = composed["title"].strip()
    summary = composed["summary"].strip()
    steps = composed["steps"]

    body = (
        f"Incident context:\n"
        f"{summary}\n\n"
        f"Resolution:\n"
    )

    body += "\n".join(
        f"{index}. {step.strip()}"
        for index, step in enumerate(steps, start=1)
    )

    return Article(
        sys_id="",
        number=article_number,
        article_id=article_number,
        title=title,
        body=body,
        category=category,
        service=service,
        workflow_state="published",
        version=1,
        security_level="human_resolution",
        section=HUMAN_RESOLUTION_SECTION,
    )
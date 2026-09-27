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

    Human-resolution articles are published for retrieval at the curated
    corpus's security level ("internal"); they are told apart by their
    KBHR-<execution_id> article number and the knowledge_capture_audit table.
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
        security_level=security_level,
        section=HUMAN_RESOLUTION_SECTION,
    )
"""Selection helpers shared by local publishing and ingestion sources."""

import json


def dedupe_articles(articles: list[dict], number_field: str = "article_number") -> list[dict]:
    """Keep one highest-version, non-retired record per article number."""
    best: dict[str, dict] = {}
    for article in articles:
        number = article[number_field]
        if article.get("workflow_state") == "retired":
            continue
        current = best.get(number)
        if current is None or _selection_key(article) > _selection_key(current):
            best[number] = article
    return [best[number] for number in sorted(best)]


def _selection_key(article: dict) -> tuple[int, str]:
    """Break equal-version ties canonically so input order cannot affect output."""
    return article.get("version", 0), json.dumps(article, sort_keys=True, separators=(",", ":"))

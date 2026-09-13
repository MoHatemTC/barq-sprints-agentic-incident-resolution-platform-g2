"""
Shared helpers for working with the raw KB article corpus (data/kb_dataset.json)
before it's transformed into either a ServiceNow publish payload or an
Article dataclass for embedding.
"""


def dedupe_articles(articles: list[dict], number_field: str = "article_number") -> list[dict]:
    """Given possibly-multiple records per article number (e.g. retired +
    published near-duplicates in the test corpus), keep only the
    highest-version, non-retired record for each article number.

    Used by both publish_kb.py (ServiceNow) and ingest.py's local JSON
    source (vector store) so a retired/superseded article never gets
    published or embedded.
    """
    best: dict[str, dict] = {}
    for article in articles:
        number = article[number_field]
        if article.get("workflow_state") == "retired":
            continue
        current = best.get(number)
        if current is None or article.get("version", 0) > current.get("version", 0):
            best[number] = article
    return list(best.values())
"""
Reads articles from a local JSON file for pipeline testing, matching the
schema Sarah confirmed: article_number, title, body, category, service,
workflow_state, version, security_level.

This is the TESTING path only. Final ingestion must pull from ServiceNow
via S1.5's Table API client -- see servicenow_source.py.
"""

import json
from pathlib import Path
from ..schema import Article


def load_articles_from_json(json_path: str) -> list[Article]:
    with open(json_path, "r", encoding="utf-8") as f:
        raw_articles = json.load(f)

    articles = []
    for raw in raw_articles:
        articles.append(Article(
            sys_id=raw.get("sys_id", raw.get("article_number", "")),  # placeholder until real sys_id exists
            number=raw.get("article_number", ""),
            article_id=raw.get("article_number", ""),
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            category=raw.get("category", ""),
            service=raw.get("service", ""),
            workflow_state=raw.get("workflow_state", ""),
            version=raw.get("version", 1),
            security_level=raw.get("security_level", ""),
        ))
    return articles
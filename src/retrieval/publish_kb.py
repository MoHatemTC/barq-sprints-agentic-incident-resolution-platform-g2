"""
Publishes the local knowledge base corpus (data/kb_dataset.json) into
ServiceNow's kb_knowledge table via the Table API, authenticated with the
OAuth integration identity from S1.2.

Idempotency note: kb_knowledge has no custom field for our article_number
(confirmed with S1.1 -- not adding one). Instead, a local mapping file
(data/servicenow_kb_mapping.json) tracks article_number -> sys_id after
each successful publish, so re-runs update the existing record instead of
duplicating it, without requiring any ServiceNow schema change.

Usage:
    python -m src.retrieval.publish_kb data/kb_dataset.json
    python -m src.retrieval.publish_kb data/kb_dataset.json --dry-run
"""

import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

import sys
import json
import argparse
import httpx
from pathlib import Path
from dotenv import load_dotenv

from .servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError

load_dotenv()

SERVICENOW_INSTANCE_URL = os.environ.get("SERVICENOW_INSTANCE_URL", "").rstrip("/")
KB_TABLE = "kb_knowledge"
MAPPING_PATH = "data/servicenow_kb_mapping.json"


def load_corpus(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_mapping(path: str = MAPPING_PATH) -> dict:
    """article_number -> sys_id. Empty dict if the file doesn't exist yet."""
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mapping(mapping: dict, path: str = MAPPING_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def _build_payload(article: dict) -> dict:
    return {
        "kb_knowledge_base": os.environ["SERVICENOW_KB_SYS_ID"],
        "short_description": article["title"],
        "text": article["body"],
        "kb_category": article.get("category", ""),
        "workflow_state": article.get("workflow_state", "draft"),
    }


def _dry_run(articles: list[dict], mapping: dict) -> dict:
    """No network call, no auth needed -- proves the corpus loads, payloads
    build correctly, and shows which articles would create vs. update
    based on the current local mapping."""
    stats = {"would_create": 0, "would_update": 0, "skipped_dry_run": 0, "failed": []}
    for article in articles:
        payload = _build_payload(article)
        action = "UPDATE" if article["article_number"] in mapping else "CREATE"
        print(f"[dry-run] would {action} {article['article_number']}: {payload['short_description']}")
        stats["would_update" if action == "UPDATE" else "would_create"] += 1
        stats["skipped_dry_run"] += 1
    return stats

def _dedupe_articles(articles: list[dict]) -> list[dict]:
    """Given possibly-multiple records per article_number (e.g. retired +
    published near-duplicates), keep only the highest-version, non-retired
    record for each article_number."""
    best: dict[str, dict] = {}
    for article in articles:
        number = article["article_number"]
        if article.get("workflow_state") == "retired":
            continue
        current = best.get(number)
        if current is None or article.get("version", 0) > current.get("version", 0):
            best[number] = article
    return list(best.values())


def publish(corpus_path: str, dry_run: bool = False) -> dict:
    articles = load_corpus(corpus_path)
    articles = _dedupe_articles(articles)
    mapping = load_mapping()

    if dry_run:
        stats = _dry_run(articles, mapping)
        print(f"\nPublish complete (dry-run): {stats}")
        return stats

    auth = ServiceNowOAuthClient()
    stats = {"created": 0, "updated": 0, "failed": []}

    with httpx.Client(timeout=15) as client:
        for article in articles:
            article_number = article["article_number"]
            payload = _build_payload(article)
            known_sys_id = mapping.get(article_number)

            try:
                if known_sys_id:
                    resp = client.patch(
                        f"{SERVICENOW_INSTANCE_URL}/api/now/table/{KB_TABLE}/{known_sys_id}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    stats["updated"] += 1
                    print(f"Updated {article_number} (sys_id={known_sys_id})")
                else:
                    resp = client.post(
                        f"{SERVICENOW_INSTANCE_URL}/api/now/table/{KB_TABLE}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    new_sys_id = resp.json()["result"]["sys_id"]
                    mapping[article_number] = new_sys_id
                    stats["created"] += 1
                    print(f"Created {article_number} (sys_id={new_sys_id})")

            except (httpx.HTTPStatusError, ServiceNowAuthError, KeyError) as e:
                stats["failed"].append({"article_number": article_number, "error": str(e)})
                print(f"FAILED {article_number}: {e}")

    save_mapping(mapping)
    print(f"\nPublish complete: {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus_path", nargs="?", default="data/kb_dataset.json")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be published without calling ServiceNow")
    args = parser.parse_args()

    try:
        publish(args.corpus_path, dry_run=args.dry_run)
    except ServiceNowAuthError as e:
        print(f"\nCannot publish: {e}")
        sys.exit(1)
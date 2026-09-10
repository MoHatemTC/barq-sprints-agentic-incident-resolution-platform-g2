"""
Publishes the local knowledge base corpus (data/kb_dataset.json) into
ServiceNow's kb_knowledge table via the Table API, authenticated with the
OAuth integration identity from S1.2.

This is idempotent: articles are matched on `number` (our article_number,
stored in ServiceNow's `u_article_number` field or similar -- see NOTE
below) and updated in place rather than duplicated on re-run.

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
from dotenv import load_dotenv

from .servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError

load_dotenv()

SERVICENOW_INSTANCE_URL = os.environ.get("SERVICENOW_INSTANCE_URL", "").rstrip("/")
KB_TABLE = "kb_knowledge"

EXTERNAL_ID_FIELD = "u_article_number"  # TODO: confirm against S1.1 field model


def load_corpus(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_existing(client: httpx.Client, auth, article_number: str):
    resp = client.get(
        f"{SERVICENOW_INSTANCE_URL}/api/now/table/{KB_TABLE}",
        headers=auth.auth_headers(),
        params={
            "sysparm_query": f"{EXTERNAL_ID_FIELD}={article_number}",
            "sysparm_fields": "sys_id",
            "sysparm_limit": "1",
        },
    )
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0]["sys_id"] if results else None


def _build_payload(article: dict) -> dict:
    return {
        EXTERNAL_ID_FIELD: article["article_number"],
        "short_description": article["title"],
        "text": article["body"],
        "kb_category": article.get("category", ""),
        "workflow_state": article.get("workflow_state", "draft"),
    }


def _dry_run(articles: list[dict]) -> dict:
    stats = {"created": 0, "updated": 0, "skipped_dry_run": 0, "failed": []}
    for article in articles:
        payload = _build_payload(article)
        print(f"[dry-run] would publish {article['article_number']}: {payload['short_description']}")
        stats["skipped_dry_run"] += 1
    return stats


def publish(corpus_path: str, dry_run: bool = False) -> dict:
    articles = load_corpus(corpus_path)

    if dry_run:
        stats = _dry_run(articles)
        print(f"\nPublish complete (dry-run): {stats}")
        return stats

    auth = ServiceNowOAuthClient()
    stats = {"created": 0, "updated": 0, "skipped_dry_run": 0, "failed": []}

    with httpx.Client(timeout=15) as client:
        for article in articles:
            article_number = article["article_number"]
            payload = _build_payload(article)
            try:
                existing_sys_id = _find_existing(client, auth, article_number)
                if existing_sys_id:
                    resp = client.patch(
                        f"{SERVICENOW_INSTANCE_URL}/api/now/table/{KB_TABLE}/{existing_sys_id}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    stats["updated"] += 1
                    print(f"Updated {article_number} (sys_id={existing_sys_id})")
                else:
                    resp = client.post(
                        f"{SERVICENOW_INSTANCE_URL}/api/now/table/{KB_TABLE}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    new_sys_id = resp.json()["result"]["sys_id"]
                    stats["created"] += 1
                    print(f"Created {article_number} (sys_id={new_sys_id})")
            except (httpx.HTTPStatusError, ServiceNowAuthError, KeyError) as e:
                stats["failed"].append({"article_number": article_number, "error": str(e)})
                print(f"FAILED {article_number}: {e}")

    print(f"\nPublish complete: {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus_path", nargs="?", default="data/kb_dataset.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        publish(args.corpus_path, dry_run=args.dry_run)
    except ServiceNowAuthError as e:
        print(f"\nCannot publish: {e}")
        sys.exit(1)

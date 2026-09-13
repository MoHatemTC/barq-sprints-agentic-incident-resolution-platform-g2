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
    python -m src.retrieval.publish_kb
    python -m src.retrieval.publish_kb --dry-run
"""

import sys
import json
import html
import argparse
import httpx
from pathlib import Path

from ..config import SERVICENOW, PATHS
from .servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError
from .sources.local_json_source import load_articles_from_json
from src.servicenow.client import _same
from src.servicenow.exceptions import ServiceNowWriteNotAppliedError

_CATEGORY_MAPPING_PATH = "data/kb_category_mapping.json"


def _load_category_mapping() -> dict:
    """
    category name -> kb_category sys_id. kb_category is a reference field,
    not free text -- a raw string like "network" gets stored as a broken
    reference (see PR discussion with Aya / mostafa on the controlled
    vocabulary decision). This mapping is a stopgap using kb_category
    records created manually in the KB until S1.1/S1.2 own this properly.
    Categories with no entry here are simply omitted from the payload
    rather than sent as a broken reference.
    """
    if not Path(_CATEGORY_MAPPING_PATH).exists():
        return {}
    with open(_CATEGORY_MAPPING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_CATEGORY_MAPPING = _load_category_mapping()


def load_mapping(path: str = None) -> dict:
    """article_number -> sys_id. Empty dict if the file doesn't exist yet."""
    path = path or PATHS.servicenow_kb_mapping
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mapping(mapping: dict, path: str = None) -> None:
    path = path or PATHS.servicenow_kb_mapping
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def _build_payload(article) -> dict:
    payload = {
        "short_description": article.title,
        "text": article.body,
        "workflow_state": article.workflow_state,
    }
    if SERVICENOW.kb_sys_id:
        payload["kb_knowledge_base"] = SERVICENOW.kb_sys_id

    category_sys_id = _CATEGORY_MAPPING.get(article.category)
    if category_sys_id:
        payload["kb_category"] = category_sys_id

    return payload


def _verify_write_applied(
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
    sys_id: str,
    payload: dict,
) -> None:
    resp = client.get(
        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{sys_id}",
        headers=auth.auth_headers(),
        params={"sysparm_fields": ",".join(payload.keys())},
    )
    resp.raise_for_status()
    result = resp.json()["result"]

    # kb_knowledge_base and kb_category are reference fields -- ServiceNow
    # returns {"link": ..., "value": ...} for these, not a bare string.
    # Extract "value" so _same() compares like-for-like with what we sent.
    reference_fields = {"kb_knowledge_base", "kb_category"}
    for field in reference_fields:
        if isinstance(result.get(field), dict):
            result[field] = result[field].get("value")

    # ServiceNow HTML-encodes text fields on write (" becomes &#34;).
    # Unescape before comparing so _same() isn't fooled by encoding.
    if "text" in result and isinstance(result["text"], str):
        result["text"] = html.unescape(result["text"])

    dropped = [
        field
        for field, value in payload.items()
        if not _same(value, result.get(field))
    ]
    if dropped:
        raise ServiceNowWriteNotAppliedError(200, f"Fields not written: {dropped}")


def _dry_run(articles, mapping: dict) -> dict:
    stats = {"would_create": 0, "would_update": 0, "skipped_dry_run": 0, "failed": []}
    for article in articles:
        payload = _build_payload(article)
        action = "UPDATE" if article.article_id in mapping else "CREATE"
        print(f"[dry-run] would {action} {article.number}: {payload['short_description']}")
        stats["would_update" if action == "UPDATE" else "would_create"] += 1
        stats["skipped_dry_run"] += 1
    return stats


def publish(corpus_path: str = None, dry_run: bool = False) -> dict:
    # dedupe_articles() already runs inside load_articles_from_json() --
    # do not call it again here on the resulting Article objects.
    articles = load_articles_from_json(corpus_path or PATHS.corpus_json)
    mapping = load_mapping()

    if dry_run:
        stats = _dry_run(articles, mapping)
        print(f"\nPublish complete (dry-run): {stats}")
        return stats

    auth = ServiceNowOAuthClient()
    stats = {"created": 0, "updated": 0, "failed": []}

    with httpx.Client(timeout=15) as client:
        for article in articles:
            payload = _build_payload(article)
            known_sys_id = mapping.get(article.article_id)

            try:
                if known_sys_id:
                    resp = client.patch(
                        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{known_sys_id}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    _verify_write_applied(client, auth, known_sys_id, payload)
                    stats["updated"] += 1
                    print(f"Updated {article.number} (sys_id={known_sys_id})")
                else:
                    resp = client.post(
                        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    new_sys_id = resp.json()["result"]["sys_id"]
                    _verify_write_applied(client, auth, new_sys_id, payload)
                    mapping[article.article_id] = new_sys_id
                    stats["created"] += 1
                    print(f"Created {article.number} (sys_id={new_sys_id})")

            except (httpx.HTTPStatusError, ServiceNowAuthError, KeyError) as e:
                stats["failed"].append({"article_number": article.number, "error": str(e)})
                print(f"FAILED {article.number}: {e}")

    save_mapping(mapping)
    print(f"\nPublish complete: {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus_path", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be published without calling ServiceNow")
    args = parser.parse_args()

    try:
        publish(args.corpus_path, dry_run=args.dry_run)
    except ServiceNowAuthError as e:
        print(f"\nCannot publish: {e}")
        sys.exit(1)
"""
Publishes the local knowledge base corpus (data/kb_dataset.json) into
ServiceNow's kb_knowledge table via the Table API, authenticated with the
OAuth integration identity from S1.2.

Idempotency: kb_knowledge has no custom field for our article_number.
A local mapping file (data/servicenow_kb_mapping.json) tracks
article_number -> sys_id. Re-runs compare mapped records first and skip exact
matches, rather than PATCHing them unnecessarily.

Write verification reuses S1.5's _same() / ServiceNowWriteNotAppliedError
(src/servicenow/client.py) against a fresh GET after every write, not the
write response body. kb_category is resolved through a local name -> sys_id
mapping. Metadata column names and an optional approved publish action are
configured rather than guessed or hard-coded.

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

def _load_category_mapping(path: str = None) -> dict:
    """
    category name -> kb_category sys_id. kb_category is a reference field,
    not free text -- a raw string like "network" gets stored as a broken
    reference (see PR discussion with Aya / mostafa on the controlled
    vocabulary decision). This mapping is a stopgap using kb_category
    records created manually in the KB until S1.1/S1.2 own this properly.
    Categories with no entry here are simply omitted from the payload
    rather than sent as a broken reference.
    """
    path = path or PATHS.servicenow_kb_category_mapping
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
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

    metadata_values = {
        "service": article.service,
        "version": article.version,
        "security_level": article.security_level,
        "article_number": article.number,
    }
    for source_field, servicenow_field in SERVICENOW.kb_metadata_field_map.items():
        payload[servicenow_field] = metadata_values[source_field]

    return payload


def _read_back(
    payload: dict, sys_id: str, client: httpx.Client, auth: ServiceNowOAuthClient
) -> dict:
    """Return the fresh Table API representation of exactly the sent fields."""
    resp = client.get(
        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{sys_id}",
        headers=auth.auth_headers(),
        params={
            "sysparm_fields": ",".join(payload.keys()),
            "sysparm_exclude_reference_link": "true",
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]


def _mismatched_fields(payload: dict, result: dict) -> list[str]:
    """Compare sent values with a Table API read-back using S1.5 normalization."""
    mismatched = []
    for field, sent in payload.items():
        got = result.get(field)
        if isinstance(got, dict):
            got = got.get("value")
        if field == "text" and isinstance(got, str):
            got = html.unescape(got)
        if not _same(sent, got):
            mismatched.append(field)
    return mismatched


def _verify_write_applied(
    payload: dict, sys_id: str, client: httpx.Client, auth: ServiceNowOAuthClient
) -> list[str]:
    """
    Read the record back fresh after a write and compare every sent field.

    Returning mismatches lets the caller invoke an explicitly configured
    publish action only for a workflow-state transition. All other dropped
    fields remain hard failures.
    """
    return _mismatched_fields(payload, _read_back(payload, sys_id, client, auth))


def _run_configured_publish_action(
    client: httpx.Client, auth: ServiceNowOAuthClient, sys_id: str
) -> None:
    """Invoke the instance-approved publish action, if one is configured."""
    path_template = SERVICENOW.kb_publish_action_path
    if not path_template:
        raise ServiceNowWriteNotAppliedError(
            200,
            "workflow_state was not applied and SERVICENOW_KB_PUBLISH_ACTION_PATH is not configured",
        )
    if not path_template.startswith("/") or "{sys_id}" not in path_template:
        raise ValueError(
            "SERVICENOW_KB_PUBLISH_ACTION_PATH must start with '/' and contain '{sys_id}'"
        )

    resp = client.post(
        f"{SERVICENOW.instance_url}{path_template.format(sys_id=sys_id)}",
        headers=auth.auth_headers(),
    )
    resp.raise_for_status()


def _verify_or_publish(
    payload: dict, sys_id: str, client: httpx.Client, auth: ServiceNowOAuthClient
) -> None:
    mismatched = _verify_write_applied(payload, sys_id, client, auth)
    if not mismatched:
        return

    if mismatched == ["workflow_state"] and payload.get("workflow_state") == "published":
        _run_configured_publish_action(client, auth, sys_id)
        mismatched = _verify_write_applied(payload, sys_id, client, auth)

    if mismatched:
        raise ServiceNowWriteNotAppliedError(200, f"Fields not written: {mismatched}")


def _mapped_record_needs_update(
    payload: dict, sys_id: str, client: httpx.Client, auth: ServiceNowOAuthClient
) -> bool:
    """Return whether a mapped record differs from the current desired payload."""
    return bool(_verify_write_applied(payload, sys_id, client, auth))


def _is_not_found(error: httpx.HTTPStatusError) -> bool:
    return error.response is not None and error.response.status_code == 404


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
    articles = load_articles_from_json(corpus_path or PATHS.corpus_json)
    mapping = load_mapping()

    if dry_run:
        stats = _dry_run(articles, mapping)
        print(f"\nPublish complete (dry-run): {stats}")
        return stats

    auth = ServiceNowOAuthClient()
    stats = {"created": 0, "updated": 0, "skipped_unchanged": 0, "failed": []}

    with httpx.Client(timeout=15) as client:
        for article in articles:
            payload = _build_payload(article)
            known_sys_id = mapping.get(article.article_id)

            try:
                if known_sys_id:
                    try:
                        needs_update = _mapped_record_needs_update(
                            payload, known_sys_id, client, auth
                        )
                    except httpx.HTTPStatusError as error:
                        if not _is_not_found(error):
                            raise
                        # The remote record was deleted outside the publisher.
                        # Drop only this stale mapping and recreate the article.
                        mapping.pop(article.article_id, None)
                        known_sys_id = None
                        needs_update = True

                if known_sys_id and not needs_update:
                    stats["skipped_unchanged"] += 1
                    print(f"Unchanged {article.number} (sys_id={known_sys_id})")
                    continue

                if known_sys_id:
                    resp = client.patch(
                        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{known_sys_id}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    sys_id = known_sys_id
                    action_label = "Updated"
                else:
                    resp = client.post(
                        f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}",
                        headers={**auth.auth_headers(), "Content-Type": "application/json"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    sys_id = resp.json()["result"]["sys_id"]
                    action_label = "Created"

                _verify_or_publish(payload, sys_id, client, auth)

                if not known_sys_id:
                    # Only record the mapping once the write is confirmed to have
                    # actually persisted -- otherwise a verification failure would
                    # still leave a bad sys_id in the mapping file (caught by
                    # test_post_read_back_dropped_non_workflow_field_raises).
                    mapping[article.article_id] = sys_id

                stats["updated" if known_sys_id else "created"] += 1
                print(f"{action_label} {article.number} (sys_id={sys_id})")

            except (httpx.HTTPStatusError, ServiceNowAuthError, KeyError, ValueError) as e:
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

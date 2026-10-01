"""
Publishes the BARQ IT Service Desk Manual (data/BARQ_IT_Service_Desk_Manual_Ed5.1.pdf)
into ServiceNow's kb_knowledge table via the Table API, authenticated with the
OAuth integration identity from S1.2. The PDF is parsed by manual_parser.py;
every section (76: chapters, KB articles, appendices) becomes one kb_knowledge
record. eval/barq_rag_eval_dataset.json is NOT published -- it is the exam.

Only published sections are sent. The retired (6.13 KB0010 v1) and archived
(6.3) sections are skipped: retrieval must never return them, and the Table API
cannot set a non-published workflow_state on this instance (the record stays
draft and verification fails).

Idempotency: kb_knowledge has no custom field for our section id.
A local mapping file (data/servicenow_kb_mapping.json) tracks
section_label -> sys_id. A new sys_id is saved the moment the record is
created, so a run that fails part-way never re-creates it next time. Re-runs
compare mapped records first and skip exact matches, rather than PATCHing them
unnecessarily. A section that fails verification is reported in
stats["failed"] and the run continues.

Write verification reuses S1.5's _same() / ServiceNowWriteNotAppliedError
(src/servicenow/client.py) against a fresh GET after every write, not the
write response body. kb_category is resolved through a local name -> sys_id
mapping. Metadata column names and an optional approved publish action are
configured rather than guessed or hard-coded.

Usage:
    python -m src.retrieval.publish_kb
    python -m src.retrieval.publish_kb --dry-run
    python -m src.retrieval.publish_kb path/to/manual.pdf
"""

import sys
import json
import html
import re
import base64
import getpass
import argparse

import httpx

from pathlib import Path

from ..config import SERVICENOW, PATHS, RETRIEVAL
from .servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError
from .manual_parser import ManualSection, parse_manual, read_pages, read_toc
from .schema import Article
from src.servicenow.client import _same
from src.servicenow.exceptions import ServiceNowWriteNotAppliedError


def _load_category_mapping(path: str = None) -> dict:
    """
    category name -> kb_category sys_id.

    kb_category is a reference field, not free text.
    Categories with no entry here are omitted from the payload.
    """
    path = path or PATHS.servicenow_kb_category_mapping

    if not Path(path).exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_CATEGORY_MAPPING = _load_category_mapping()


FORMATTED_PATH = Path("data/manual_formatted.json")   # written by tools/format_manual.py


def _load_formatted() -> dict:
    """section_label -> readable article HTML (sections not yet formatted are missing)."""
    if not FORMATTED_PATH.exists():
        return {}
    entries = json.loads(FORMATTED_PATH.read_text(encoding="utf-8"))
    return {label: e["html"] for label, e in entries.items() if e.get("html")}


def article_title(section: ManualSection, chapters: dict[str, str]) -> str:
    """'KB0004 – Print jobs…', 'Appendix B.4 – Knowledge article proposal', '9.3 Timeline – Major incident report…'."""
    if section.kb_number:
        return section.title
    name = section.title.lstrip("· ").strip()
    sid = section.section_id
    if sid.startswith("Appendix"):
        name = re.sub(rf"^{re.escape(sid.removeprefix('Appendix '))}\s+", "", name)   # "B.4 Knowledge…" -> "Knowledge…"
        return f"{sid} – {name}"
    if not name:
        return sid                                                        # "Document control"
    chapter, _, sub = sid.partition(".")
    if not sub:
        return f"Chapter {sid} – {name}"
    return f"{sid} {name} – {chapters[chapter]}" if chapters.get(chapter) else f"{sid} {name}"


def section_to_article(section: ManualSection, chapters: dict[str, str] | None = None,
                       body: str | None = None) -> Article:
    """One manual section -> the canonical Article the payload is built from."""
    return Article(
        sys_id="",
        number=section.kb_number or section.section_label,
        article_id=section.section_label,  # unique per section, incl. "6.13 KB0010 v1" / "v2"
        title=article_title(section, chapters or {}),
        body=body or html.escape(section.text, quote=False),   # always HTML
        category=section.category,
        service=section.service,
        workflow_state=section.workflow_state,
        version=section.version,
        security_level=section.security_level,
    )


def load_manual_articles(pdf_path: str = None) -> list[Article]:
    pdf_path = pdf_path or RETRIEVAL.manual_pdf_path
    chapters = {sid: title for sid, title in read_toc(read_pages(pdf_path)).items() if sid.isdigit()}
    formatted = _load_formatted()
    return [section_to_article(s, chapters, formatted.get(s.section_label)) for s in parse_manual(pdf_path)]


def _split_publishable(articles: list[Article]) -> tuple[list[Article], list[Article]]:
    """(published, skipped) -- only published sections go to ServiceNow."""
    published = [a for a in articles if a.workflow_state == "published"]
    skipped = [a for a in articles if a.workflow_state != "published"]
    for article in skipped:
        print(f"Skipped {article.article_id} ({article.workflow_state}, not published)")
    return published, skipped


def load_mapping(path: str = None) -> dict:
    """section_label -> sys_id. Empty dict if the file doesn't exist yet."""
    path = path or PATHS.servicenow_kb_mapping

    if not Path(path).exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mapping(mapping: dict, path: str = None) -> None:
    path = path or PATHS.servicenow_kb_mapping

    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def _build_payload(article, body_is_html: bool = False) -> dict:
    """
    Convert the canonical Article into the ServiceNow kb_knowledge payload.
    Manual articles are already HTML (body_is_html); other bodies are plain text.
    """
    payload = {
        "short_description": article.title,
        "text": article.body if body_is_html else html.escape(article.body, quote=False),
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
    payload: dict,
    sys_id: str,
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
) -> dict:
    """Return the fresh Table API representation of exactly the sent fields."""
    resp = client.get(
        f"{SERVICENOW.instance_url}/api/now/table/"
        f"{SERVICENOW.kb_table}/{sys_id}",
        headers=auth.auth_headers(),
        params={
            "sysparm_fields": ",".join(payload.keys()),
            "sysparm_exclude_reference_link": "true",
        },
    )

    resp.raise_for_status()

    return resp.json()["result"]


def _normalize_html(text: str) -> str:
    """ServiceNow's rewrite of the same HTML: no whitespace between tags, <br />, implied <tbody>."""
    text = re.sub(r">\s+<", "><", html.unescape(text)).strip()
    return re.sub(r"</?tbody>", "", re.sub(r"<br\s*/?>", "<br>", text))


def _mismatched_fields(payload: dict, result: dict) -> list[str]:
    """Compare sent values with a Table API read-back using S1.5 normalization."""
    mismatched = []

    for field, sent in payload.items():
        got = result.get(field)

        if isinstance(got, dict):
            got = got.get("value")

        if field == "text" and isinstance(got, str):
            # ServiceNow re-encodes entities and drops whitespace between tags ("<table>\n<tr>")
            got, sent = _normalize_html(got), _normalize_html(sent)
        if not _same(sent, got):
            mismatched.append(field)

    return mismatched


def _verify_write_applied(
    payload: dict,
    sys_id: str,
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
) -> list[str]:
    """
    Read the record back fresh after a write and compare every sent field.

    Returning mismatches lets the caller invoke an explicitly configured
    publish action only for a workflow-state transition.
    """
    return _mismatched_fields(
        payload,
        _read_back(payload, sys_id, client, auth),
    )


def _run_configured_publish_action(
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
    sys_id: str,
) -> None:
    """Invoke the instance-approved publish action, if one is configured."""
    path_template = SERVICENOW.kb_publish_action_path

    if not path_template:
        raise ServiceNowWriteNotAppliedError(
            200,
            "workflow_state was not applied and "
            "SERVICENOW_KB_PUBLISH_ACTION_PATH is not configured",
        )

    if not path_template.startswith("/") or "{sys_id}" not in path_template:
        raise ValueError(
            "SERVICENOW_KB_PUBLISH_ACTION_PATH must start with '/' "
            "and contain '{sys_id}'"
        )

    resp = client.post(
        f"{SERVICENOW.instance_url}"
        f"{path_template.format(sys_id=sys_id)}",
        headers=auth.auth_headers(),
    )

    resp.raise_for_status()


def _verify_or_publish(
    payload: dict,
    sys_id: str,
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
) -> None:
    """
    Verify the ServiceNow write.

    If only workflow_state failed and the desired state is published,
    invoke the configured publish action and verify again.
    """
    mismatched = _verify_write_applied(
        payload,
        sys_id,
        client,
        auth,
    )

    if not mismatched:
        return

    if (
        mismatched == ["workflow_state"]
        and payload.get("workflow_state") == "published"
    ):
        _run_configured_publish_action(
            client,
            auth,
            sys_id,
        )

        mismatched = _verify_write_applied(
            payload,
            sys_id,
            client,
            auth,
        )

    if mismatched:
        raise ServiceNowWriteNotAppliedError(
            200,
            f"Fields not written: {mismatched}",
        )


def _mapped_record_needs_update(
    payload: dict,
    sys_id: str,
    client: httpx.Client,
    auth: ServiceNowOAuthClient,
) -> bool:
    """Return whether a mapped record differs from the current desired payload."""
    return bool(
        _verify_write_applied(
            payload,
            sys_id,
            client,
            auth,
        )
    )


def _is_not_found(error: httpx.HTTPStatusError) -> bool:
    return (
        error.response is not None
        and error.response.status_code == 404
    )


def _dry_run(articles, mapping: dict, skipped: int) -> dict:
    stats = {"would_create": 0, "would_update": 0, "skipped_dry_run": 0,
             "skipped_not_published": skipped, "failed": []}
    for article in articles:
        payload = _build_payload(article, body_is_html=True)
        action = "UPDATE" if article.article_id in mapping else "CREATE"
        print(f"[dry-run] would {action} {article.article_id} [{payload['workflow_state']}]: "
              f"{payload['short_description']}")
        stats["would_update" if action == "UPDATE" else "would_create"] += 1
        stats["skipped_dry_run"] += 1

    return stats


def publish_article(article) -> dict:
    """
    S3.5: publish one canonical Article to ServiceNow and verify the persisted record.

    The single-article write-back path of the human-resolution knowledge capture.
    Reuses the corpus publisher's OAuth identity, payload, article -> sys_id mapping,
    idempotency and read-back verification, so there is one way to write the KB.
    """
    mapping = load_mapping()
    auth = ServiceNowOAuthClient()
    payload = _build_payload(article)
    known_sys_id = mapping.get(article.article_id)

    with httpx.Client(timeout=15) as client:
        if known_sys_id:
            try:
                needs_update = _mapped_record_needs_update(payload, known_sys_id, client, auth)
            except httpx.HTTPStatusError as error:
                if not _is_not_found(error):
                    raise
                # The ServiceNow record was deleted externally: recreate it.
                mapping.pop(article.article_id, None)
                known_sys_id = None
                needs_update = True

            if not needs_update:
                return {"status": "unchanged", "article_number": article.number, "sys_id": known_sys_id}

        if known_sys_id:
            response = client.patch(
                f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{known_sys_id}",
                headers={**auth.auth_headers(), "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            sys_id, status = known_sys_id, "updated"
        else:
            response = client.post(
                f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}",
                headers={**auth.auth_headers(), "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            sys_id, status = response.json()["result"]["sys_id"], "created"
            # Record the record now: if verification below fails, a retry
            # PATCHes this sys_id instead of creating a duplicate article.
            mapping[article.article_id] = sys_id
            save_mapping(mapping)

        # Never report success until ServiceNow is read back and verified.
        _verify_or_publish(payload, sys_id, client, auth)

    return {"status": status, "article_number": article.number, "sys_id": sys_id}


class AdminBasicAuth:
    def __init__(self, username: str, password: str):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def auth_headers(self) -> dict:
        return dict(self._headers)


def publish(pdf_path: str = None, dry_run: bool = False, auth=None) -> dict:
    articles, skipped = _split_publishable(load_manual_articles(pdf_path))
    mapping = load_mapping()

    if dry_run:
        stats = _dry_run(articles, mapping, len(skipped))
        print(f"\nPublish complete (dry-run): {stats}")
        return stats

    auth = auth or ServiceNowOAuthClient()
    stats = {"created": 0, "updated": 0, "skipped_unchanged": 0,
             "skipped_not_published": len(skipped), "failed": []}

    try:
        _publish_articles(articles, mapping, stats, auth)
    finally:
        save_mapping(mapping)
    print(f"\nPublish complete: {stats}")
    return stats


def _publish_articles(articles, mapping: dict, stats: dict, auth: ServiceNowOAuthClient) -> None:
    with httpx.Client(timeout=15) as client:

        for article in articles:
            payload = _build_payload(article, body_is_html=True)
            known_sys_id = mapping.get(article.article_id)

            try:
                if known_sys_id:
                    try:
                        needs_update = _mapped_record_needs_update(
                            payload,
                            known_sys_id,
                            client,
                            auth,
                        )

                    except httpx.HTTPStatusError as error:
                        if not _is_not_found(error):
                            raise

                        # The remote record was deleted outside the publisher.
                        mapping.pop(article.article_id, None)
                        known_sys_id = None
                        needs_update = True

                if known_sys_id and not needs_update:
                    stats["skipped_unchanged"] += 1
                    print(f"Unchanged {article.article_id} (sys_id={known_sys_id})")
                    continue

                if known_sys_id:
                    response = client.patch(
                        f"{SERVICENOW.instance_url}/api/now/table/"
                        f"{SERVICENOW.kb_table}/{known_sys_id}",
                        headers={
                            **auth.auth_headers(),
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                    response.raise_for_status()

                    sys_id = known_sys_id
                    action_label = "Updated"

                else:
                    response = client.post(
                        f"{SERVICENOW.instance_url}/api/now/table/"
                        f"{SERVICENOW.kb_table}",
                        headers={
                            **auth.auth_headers(),
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                    response.raise_for_status()

                    sys_id = response.json()["result"]["sys_id"]
                    action_label = "Created"
                    # Record the record now: if verification below fails, the
                    # next run PATCHes this sys_id instead of creating a duplicate.
                    mapping[article.article_id] = sys_id
                    save_mapping(mapping)

                # Verify using a fresh ServiceNow GET.
                _verify_or_publish(
                    payload,
                    sys_id,
                    client,
                    auth,
                )

                stats["updated" if known_sys_id else "created"] += 1
                print(f"{action_label} {article.article_id} (sys_id={sys_id})")

            except (httpx.HTTPStatusError, ServiceNowAuthError, ServiceNowWriteNotAppliedError,
                    KeyError, ValueError) as e:
                stats["failed"].append({"section": article.article_id, "error": str(e)})
                print(f"FAILED {article.article_id}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", nargs="?", default=None,
                        help="defaults to MANUAL_PDF_PATH")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be published without calling ServiceNow")
    parser.add_argument("--admin", action="store_true",
                        help="Prompt for a ServiceNow admin username/password (needed to edit published articles)")
    args = parser.parse_args()

    auth = None
    if args.admin:
        auth = AdminBasicAuth(input("ServiceNow admin username: ").strip(),
                              getpass.getpass("ServiceNow admin password: "))
    try:
        publish(args.pdf_path, dry_run=args.dry_run, auth=auth)
    except ServiceNowAuthError as e:
        print(f"\nCannot publish: {e}")
        sys.exit(1)

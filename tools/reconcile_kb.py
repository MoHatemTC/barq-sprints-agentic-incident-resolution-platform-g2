"""
Make ServiceNow's knowledge base hold exactly the BARQ manual, filed under incident categories.
Dry run by default; nothing is written to ServiceNow without --apply. The local mapping
files are always written (they are gitignored and only describe what is already there).
Afterwards, refresh Qdrant:
  python tools/reconcile_kb.py            # dry run: print the plan
  python tools/reconcile_kb.py --apply --admin
  python -m src.retrieval.ingest sync

Published articles cannot be edited on this instance, even by an admin. To re-file the manual
(new categories, titles, text), delete and recreate it:

  python tools/reconcile_kb.py --apply --admin --recreate-manual
  python -m src.retrieval.publish_kb
  python -m src.retrieval.ingest sync
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import INCIDENT_CATEGORIES, PATHS, SERVICENOW  # noqa: E402
from src.retrieval.manual_parser import parse_manual  # noqa: E402
from src.retrieval.servicenow_auth import ServiceNowOAuthClient  # noqa: E402

TABLE_API = f"{SERVICENOW.instance_url}/api/now/table"
BACKUP_DIR = REPO / "data" / "backup"


def _get_all(client: httpx.Client, headers: dict, table: str, query: str, fields: list[str]) -> list[dict]:
    rows, offset = [], 0
    while True:
        r = client.get(f"{TABLE_API}/{table}", headers=headers, params={
            "sysparm_query": f"{query}^ORDERBYsys_created_on",
            "sysparm_fields": ",".join(fields),
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": 200,
            "sysparm_offset": offset,
        })
        r.raise_for_status()
        batch = r.json().get("result") or []
        rows += batch
        offset += 200
        if not batch or int(r.headers.get("X-Total-Count", 0)) <= offset:
            return rows


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def ensure_categories(client, headers, needed: set[str], apply: bool) -> dict[str, str]:
    """kb_category label -> sys_id under our KB, creating the missing `needed` labels."""
    rows = _get_all(client, headers, "kb_category",
                    f"parent_table=kb_knowledge_base^parent_id={SERVICENOW.kb_sys_id}",
                    ["sys_id", "label"])
    mapping = {r["label"]: r["sys_id"] for r in rows}
    for name in sorted(needed - set(mapping)):
        if not apply:
            print(f"[dry-run] would create kb_category {name!r}")
            continue
        r = client.post(f"{TABLE_API}/kb_category", headers=headers, json={
            "label": name, "value": name, "active": "true",
            "parent_table": "kb_knowledge_base", "parent_id": SERVICENOW.kb_sys_id,
        })
        r.raise_for_status()
        mapping[name] = r.json()["result"]["sys_id"]
        print(f"Created kb_category {name!r} ({mapping[name]})")
    return mapping


def reconcile(apply: bool, admin_auth: httpx.BasicAuth | None = None, recreate_manual: bool = False) -> dict:
    if not SERVICENOW.kb_sys_id:
        raise SystemExit("SERVICENOW_KB_SYS_ID is not set; refusing to touch every KB on the instance.")
    number_field = SERVICENOW.kb_metadata_field_map.get("article_number")
    if not number_field:
        raise SystemExit("SERVICENOW_KB_METADATA_FIELD_MAP has no article_number column.")

    sections = [s for s in parse_manual() if s.workflow_state == "published"]
    # publish_kb writes number = kb_number or section_label, and keys its mapping by section_label.
    label_for_number = {s.kb_number or s.section_label: s.section_label for s in sections}

    # --admin: an admin's basic auth for this run only (the integration user gets 403 on
    # published articles). Otherwise the integration user's OAuth token from .env.
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if not admin_auth:
        headers.update(ServiceNowOAuthClient().auth_headers())
    with httpx.Client(timeout=30, auth=admin_auth) as client:
        categories = ensure_categories(client, headers, set(INCIDENT_CATEGORIES), apply)
        _save_json(Path(PATHS.servicenow_kb_category_mapping), categories)

        records = _get_all(client, headers, "kb_knowledge",
                           f"kb_knowledge_base={SERVICENOW.kb_sys_id}",
                           ["sys_id", "number", "short_description", "workflow_state",
                            "kb_category", "sys_created_on", "sys_created_by", number_field])

        mapping, extra = {}, []
        for r in records:
            label = label_for_number.get(r.get(number_field, ""))
            if label and label not in mapping and r["workflow_state"] == "published":
                mapping[label] = r["sys_id"]
            elif not str(r.get(number_field, "")).startswith("KBHR-"):   # human-approved: kept
                extra.append(r)
        missing = sorted({s.section_label for s in sections} - set(mapping))
        print(f"\nManual sections in ServiceNow: {len(mapping)}/{len(sections)}"
              + (f"  missing: {missing}" if missing else ""))
        print(f"Records not from the manual: {len(extra)}")
        for r in extra:
            print(f"  {r['number']}  {r.get(number_field, '')!s:44} {r['workflow_state']:9} "
                  f"{r['short_description'][:60]}")

        # --recreate-manual: published articles cannot be edited on this instance (ACL, even for
        # admin), so the manual's are deleted here and publish_kb creates them again.
        manual = [r for r in records if r["sys_id"] in set(mapping.values())] if recreate_manual else []
        if manual:
            print(f"Manual sections to recreate: {len(manual)}")
            extra = extra + manual
        _save_json(Path(PATHS.servicenow_kb_mapping), {} if manual and apply else mapping)

        blocked = []
        if extra and apply:
            backup = BACKUP_DIR / f"kb_deleted-{datetime.now():%Y%m%d-%H%M%S}.json"
            # Full records, so any of them can be recreated by hand if needed.
            full = [client.get(f"{TABLE_API}/kb_knowledge/{r['sys_id']}", headers=headers).json()["result"]
                    for r in extra]
            _save_json(backup, full)
            print(f"\nBacked up {len(full)} records to {backup.relative_to(REPO)}")
            for r in extra:
                resp = client.delete(f"{TABLE_API}/kb_knowledge/{r['sys_id']}", headers=headers)
                if resp.status_code == 403:   # the integration user may not delete published articles
                    blocked.append(r["number"])
                    continue
                resp.raise_for_status()
                print(f"Deleted {r['number']} ({r.get(number_field, '')})")
            if blocked:
                print(f"\n403 on {len(blocked)} records; re-run with --admin: {', '.join(blocked)}")
        elif extra:
            print("\n[dry-run] re-run with --apply to back these up and delete them")


    return {"in_servicenow": len(mapping), "manual_sections": len(sections), "missing": missing,
            "to_delete": len(extra), "delete_blocked": len(blocked), "applied": apply}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="create categories and delete non-manual records")
    ap.add_argument("--recreate-manual", action="store_true",
                    help="also delete the manual's articles so publish_kb creates them again")
    ap.add_argument("--admin", action="store_true",
                    help="prompt for a ServiceNow admin username/password (used for this run only)")
    args = ap.parse_args()
    auth = None
    if args.admin:
        user = input("ServiceNow admin username: ").strip()
        auth = httpx.BasicAuth(user, getpass.getpass("ServiceNow admin password: "))
    print(reconcile(args.apply, auth, args.recreate_manual))

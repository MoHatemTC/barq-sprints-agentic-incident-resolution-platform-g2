"""Wipe the configured ServiceNow Knowledge Base so it can be republished clean.

Usage (from the project root):
    python -m scripts.reset_kb                 # dry run
    python -m scripts.reset_kb --yes           # delete with the OAuth integration user
    python -m scripts.reset_kb --yes --admin   # delete as a ServiceNow admin (asks for login)

Then republish:
    python -m src.retrieval.publish_kb
    python -m src.retrieval.ingest sync
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from src.config import PATHS, QDRANT, SERVICENOW
from src.retrieval.servicenow_auth import ServiceNowOAuthClient

PAGE = 100


def fetch_all(client: httpx.Client, headers: dict) -> list[dict]:
    url = f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}"
    records, offset = [], 0
    while True:
        resp = client.get(
            url,
            headers=headers,
            params={
                "sysparm_query": f"kb_knowledge_base={SERVICENOW.kb_sys_id}^ORDERBYsys_id",
                "sysparm_limit": PAGE,
                "sysparm_offset": offset,
                "sysparm_exclude_reference_link": "true",
            },
        )
        resp.raise_for_status()
        batch = resp.json()["result"]
        records.extend(batch)
        if len(batch) < PAGE:
            return records
        offset += PAGE


def cleanup_local(stamp: str) -> None:
    """Move the article->sys_id mapping aside and drop the Qdrant collection."""
    mapping = Path(PATHS.servicenow_kb_mapping)
    if mapping.exists():
        bak = mapping.with_name(f"{mapping.stem}.bak-{stamp}{mapping.suffix}")
        mapping.rename(bak)
        print(f"\nMapping moved to {bak}")

    try:
        from qdrant_client import QdrantClient

        QdrantClient(url=QDRANT.url, check_compatibility=False).delete_collection(QDRANT.collection_name)
        print(f"Qdrant collection '{QDRANT.collection_name}' dropped")
    except Exception as exc:
        print(f"Could not drop the Qdrant collection ({exc}). Drop it manually before syncing.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    parser.add_argument("--admin", action="store_true",
                        help="use a ServiceNow admin username/password (asked interactively)")
    args = parser.parse_args()

    if not SERVICENOW.kb_sys_id:
        print("SERVICENOW_KB_SYS_ID is empty. Refusing to run: without it this could "
              "match every knowledge base in the instance.")
        return 1

    if args.admin:
        import getpass

        user = input("ServiceNow admin username: ").strip()
        password = getpass.getpass("ServiceNow admin password (hidden): ")
        headers = {"Accept": "application/json"}
        client_kwargs = {"auth": (user, password)}
    else:
        headers = {**ServiceNowOAuthClient().auth_headers(), "Accept": "application/json"}
        client_kwargs = {}

    with httpx.Client(timeout=30, **client_kwargs) as client:
        records = fetch_all(client, headers)
        print(f"Found {len(records)} articles in knowledge base {SERVICENOW.kb_sys_id}\n")
        for r in sorted(records, key=lambda x: x.get("number", "")):
            print(f"  {r.get('number', '?'):<10} {r.get('workflow_state', '?'):<10} {r.get('short_description', '')[:70]}")

        if not records:
            print("\nNo articles left in ServiceNow.")
            if not args.yes:
                print("Re-run with --yes to also clear the local mapping and the Qdrant collection.")
                return 0
            cleanup_local(time.strftime("%Y%m%d_%H%M%S"))
            print("\nDone. Next:\n  python -m src.retrieval.publish_kb\n  python -m src.retrieval.ingest sync")
            return 0

        backup_dir = Path("data/backup")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"kb_backup_{stamp}.json"
        backup_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nBackup saved: {backup_path}")

        if not args.yes:
            print("\nDry run only. Re-run with --yes to delete.")
            return 0

        typed = input(f"\nType {len(records)} to delete these {len(records)} articles from ServiceNow: ").strip()
        if typed != str(len(records)):
            print("Count did not match. Nothing deleted.")
            return 1

        failed = []
        for i, r in enumerate(records, 1):
            resp = client.delete(
                f"{SERVICENOW.instance_url}/api/now/table/{SERVICENOW.kb_table}/{r['sys_id']}",
                headers=headers,
            )
            if resp.status_code in (200, 204, 404):
                print(f"[{i}/{len(records)}] deleted {r.get('number', r['sys_id'])}")
            else:
                failed.append((r.get("number", r["sys_id"]), resp.status_code))
                print(f"[{i}/{len(records)}] FAILED {r.get('number', r['sys_id'])}: HTTP {resp.status_code}")

    if failed:
        print(f"\n{len(failed)} deletions failed: {failed}")
        if any(code in (401, 403) for _, code in failed):
            print("HTTP 401/403: this user is not allowed to delete kb_knowledge. "
                  "Retry with --admin, or use Scripts - Background in ServiceNow.")
        print("Mapping file and Qdrant were NOT touched. Fix the errors and run again.")
        return 2

    cleanup_local(stamp)
    print("\nDone. Next:\n  python -m src.retrieval.publish_kb\n  python -m src.retrieval.ingest sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
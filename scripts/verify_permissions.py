"""
Verifies exactly what the ServiceNow OAuth integration identity can do,
by executed attempt -- not by reading intended ACL configuration.

This exists because "the identity is supposed to have access" and "the
identity was observed successfully calling the API" are different claims.
Only the second is evidence.

Usage:
    python scripts/verify_permissions.py
"""

import os
import sys
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import SERVICENOW
from src.retrieval.servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError


def check_authentication(auth: ServiceNowOAuthClient) -> bool:
    try:
        auth.get_token()
        print("[PASS] OAuth authentication succeeded (token issued).")
        return True
    except ServiceNowAuthError as e:
        print(f"[FAIL] OAuth authentication failed: {e}")
        return False


def check_read(client: httpx.Client, auth: ServiceNowOAuthClient, table: str) -> bool:
    resp = client.get(
        f"{SERVICENOW.instance_url}/api/now/table/{table}",
        headers=auth.auth_headers(),
        params={"sysparm_limit": "1"},
    )
    ok = resp.status_code == 200
    print(f"[{'PASS' if ok else 'FAIL'}] READ  {table}: HTTP {resp.status_code}")
    return ok


def check_write(client: httpx.Client, auth: ServiceNowOAuthClient, table: str) -> bool:
    payload = {
        "short_description": "PERMISSION VERIFICATION TEST - safe to delete",
        "text": "Created by scripts/verify_permissions.py to prove write access. Delete freely.",
        "workflow_state": "draft",
    }
    if SERVICENOW.kb_sys_id:
        payload["kb_knowledge_base"] = SERVICENOW.kb_sys_id

    resp = client.post(
        f"{SERVICENOW.instance_url}/api/now/table/{table}",
        headers={**auth.auth_headers(), "Content-Type": "application/json"},
        json=payload,
    )
    ok = resp.status_code == 201
    print(f"[{'PASS' if ok else 'FAIL'}] WRITE {table}: HTTP {resp.status_code}"
          + (f" -- {resp.json().get('error', {}).get('message', '')}" if not ok else ""))

    if ok:
        sys_id = resp.json()["result"]["sys_id"]
        cleanup = client.delete(
            f"{SERVICENOW.instance_url}/api/now/table/{table}/{sys_id}",
            headers=auth.auth_headers(),
        )
        cleanup_ok = cleanup.status_code == 204
        print(f"       cleanup (delete test record): "
              f"{'ok' if cleanup_ok else 'FAILED -- manual cleanup needed, sys_id=' + sys_id}")

    return ok


def main():
    auth = ServiceNowOAuthClient()

    print("=== ServiceNow OAuth Identity Permission Verification ===\n")

    if not check_authentication(auth):
        sys.exit(1)

    with httpx.Client(timeout=15) as client:
        print()
        check_read(client, auth, "incident")
        check_read(client, auth, SERVICENOW.kb_table)
        print()
        check_write(client, auth, SERVICENOW.kb_table)

    print("\nThis output -- not the ACL configuration screen -- is the evidence "
          "of what this identity can actually do.")


if __name__ == "__main__":
    main()
"""
<<<<<<< HEAD
Verifies exactly what the ServiceNow OAuth integration identity
(ai_orchestrator_svc) can do, by executed attempt -- not by reading
intended ACL configuration.

This exists because "the identity is supposed to have access" and "the
identity was observed successfully calling the API" are different
claims. Only the second is evidence.

This script merges two things into one run:

  PART A - Identity capability sanity check (broad):
    - Can we authenticate at all?
    - Can we read the incident table?
    - Can we read/write the knowledge base table (with cleanup)?

  PART B - Incident field-level ACL verification (fine-grained):
    - Confirms the identity has exactly the access it should have on
      a specific Incident record: allowed fields succeed, denied
      fields are silently blocked.
    - HTTP 200 is never treated as proof by itself -- every check
      compares a "before" value against an "after" value, because
      ServiceNow returns 200 even when it silently rejects a write to
      a specific field.
    - Journal fields (work_notes, comments) are measured by counting
      rows in sys_journal_field, not by reading the field value back,
      because the Table API always returns Journal fields empty
      regardless of success or failure. A short delay
      (JOURNAL_WRITE_DELAY_SECONDS) is applied before reading the
      journal back, because ServiceNow commits Journal entries
      slightly after the Table API PATCH response returns -- reading
      immediately can misreport a real success as FAIL.
=======
Verifies exactly what the ServiceNow OAuth integration identity can do,
by executed attempt -- not by reading intended ACL configuration.

This exists because "the identity is supposed to have access" and "the
identity was observed successfully calling the API" are different claims.
Only the second is evidence.
>>>>>>> origin/s2.1/fast-api-webhook

Usage:
    python scripts/verify_permissions.py
"""

import os
import sys
<<<<<<< HEAD
import time

import httpx
from dotenv import load_dotenv
=======
import httpx
>>>>>>> origin/s2.1/fast-api-webhook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.config import SERVICENOW
from src.retrieval.servicenow_auth import ServiceNowOAuthClient, ServiceNowAuthError


<<<<<<< HEAD
load_dotenv()

# Only needed for the fine-grained incident field tests in Part B --
# Part A does not touch a specific incident record.
INCIDENT_SYS_ID = os.getenv("INCIDENT_SYS_ID")

EXECUTION_LOG_URL = (
    f"{SERVICENOW.instance_url}/api/now/table/"
    "x_2215689_ai_inc_0_ai_execution_log"
)

# How long to wait (seconds) after writing to a Journal field before
# reading sys_journal_field back, to avoid a race condition between
# the PATCH response and the journal entry actually being committed.
JOURNAL_WRITE_DELAY_SECONDS = 3

# All test outcomes (from both Part A and Part B) collected here so a
# single final summary can be printed at the end.
# Each entry is (test_name, PASS/FAIL, optional note)
RESULTS = []


def record_result(name, status, note=""):
    """Record a test outcome into the shared RESULTS list."""
    RESULTS.append((name, status, note))


def incident_url():
    if not INCIDENT_SYS_ID:
        print("INCIDENT_SYS_ID is not set -- Part B (field-level ACL "
              "tests) needs a specific incident record to test against.")
        sys.exit(1)
    return f"{SERVICENOW.instance_url}/api/now/table/incident/{INCIDENT_SYS_ID}"


# ---------------------------------------------------------------------------
# PART A -- identity capability sanity check
# ---------------------------------------------------------------------------

=======
>>>>>>> origin/s2.1/fast-api-webhook
def check_authentication(auth: ServiceNowOAuthClient) -> bool:
    try:
        auth.get_token()
        print("[PASS] OAuth authentication succeeded (token issued).")
<<<<<<< HEAD
        record_result("Auth: token issued", "PASS")
        return True
    except ServiceNowAuthError as e:
        print(f"[FAIL] OAuth authentication failed: {e}")
        record_result("Auth: token issued", "FAIL", str(e))
=======
        return True
    except ServiceNowAuthError as e:
        print(f"[FAIL] OAuth authentication failed: {e}")
>>>>>>> origin/s2.1/fast-api-webhook
        return False


def check_read(client: httpx.Client, auth: ServiceNowOAuthClient, table: str) -> bool:
    resp = client.get(
        f"{SERVICENOW.instance_url}/api/now/table/{table}",
        headers=auth.auth_headers(),
        params={"sysparm_limit": "1"},
    )
    ok = resp.status_code == 200
    print(f"[{'PASS' if ok else 'FAIL'}] READ  {table}: HTTP {resp.status_code}")
<<<<<<< HEAD
    record_result(f"Read table: {table}", "PASS" if ok else "FAIL", f"HTTP {resp.status_code}")
=======
>>>>>>> origin/s2.1/fast-api-webhook
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
<<<<<<< HEAD
    note = f"HTTP {resp.status_code}"
    if not ok:
        note += f" -- {resp.json().get('error', {}).get('message', '')}"
    print(f"[{'PASS' if ok else 'FAIL'}] WRITE {table}: {note}")
=======
    print(f"[{'PASS' if ok else 'FAIL'}] WRITE {table}: HTTP {resp.status_code}"
          + (f" -- {resp.json().get('error', {}).get('message', '')}" if not ok else ""))
>>>>>>> origin/s2.1/fast-api-webhook

    if ok:
        sys_id = resp.json()["result"]["sys_id"]
        cleanup = client.delete(
            f"{SERVICENOW.instance_url}/api/now/table/{table}/{sys_id}",
            headers=auth.auth_headers(),
        )
        cleanup_ok = cleanup.status_code == 204
        print(f"       cleanup (delete test record): "
              f"{'ok' if cleanup_ok else 'FAILED -- manual cleanup needed, sys_id=' + sys_id}")
<<<<<<< HEAD
        if not cleanup_ok:
            note += f"; cleanup FAILED, sys_id={sys_id}"

    record_result(f"Write table: {table}", "PASS" if ok else "FAIL", note)
    return ok


def run_identity_capability_checks(auth: ServiceNowOAuthClient) -> bool:
    print("=" * 50)
    print("PART A: Identity capability sanity check")
    print("=" * 50)

    if not check_authentication(auth):
        return False
=======

    return ok


def main():
    auth = ServiceNowOAuthClient()

    print("=== ServiceNow OAuth Identity Permission Verification ===\n")

    if not check_authentication(auth):
        sys.exit(1)
>>>>>>> origin/s2.1/fast-api-webhook

    with httpx.Client(timeout=15) as client:
        print()
        check_read(client, auth, "incident")
        check_read(client, auth, SERVICENOW.kb_table)
        print()
        check_write(client, auth, SERVICENOW.kb_table)

<<<<<<< HEAD
    return True


# ---------------------------------------------------------------------------
# PART B -- incident field-level ACL verification
# ---------------------------------------------------------------------------

def get_incident(auth: ServiceNowOAuthClient, client: httpx.Client):
    response = client.get(incident_url(), headers=auth.auth_headers())

    if response.status_code != 200:
        print(response.text)
        sys.exit(1)

    return response.json()["result"]


def update_incident(auth: ServiceNowOAuthClient, client: httpx.Client, payload):
    return client.patch(incident_url(), headers=auth.auth_headers(), json=payload)


def get_journal_count(auth: ServiceNowOAuthClient, client: httpx.Client, element):
    """
    Count journal records. Journal fields are stored in
    sys_journal_field. Incident inherits from Task, so we do not
    filter by table name.
    """
    url = (
        f"{SERVICENOW.instance_url}/api/now/table/sys_journal_field"
        f"?sysparm_query="
        f"element={element}"
        f"^element_id={INCIDENT_SYS_ID}"
        f"&sysparm_limit=100"
    )

    response = client.get(url, headers=auth.auth_headers())

    if response.status_code != 200:
        print("Failed reading journal fields")
        print(response.text)
        sys.exit(1)

    return len(response.json()["result"])


def get_journal_entries(auth: ServiceNowOAuthClient, client: httpx.Client, element):
    url = (
        f"{SERVICENOW.instance_url}/api/now/table/sys_journal_field"
        f"?sysparm_query=element_id={INCIDENT_SYS_ID}"
        f"^element={element}"
        f"&sysparm_limit=100"
        f"&sysparm_fields=sys_id,element,element_id,value,sys_created_on,sys_created_by"
    )

    response = client.get(url, headers=auth.auth_headers())

    print("=" * 50)
    print(f"Journal entries for: {element}")
    print("(Fetches the actual Journal records stored for this element on the Incident - i.e. whether a work note was truly written or not)")
    print("HTTP Status:", response.status_code)
    print("(200 just means the request itself succeeded, not that there is any data inside)")
    print("Response body (if the result is [] empty, it means not a single Journal Entry was actually recorded):")
    print(response.text)

    return response


def test_read_incident(auth: ServiceNowOAuthClient, client: httpx.Client):
    response = client.get(incident_url(), headers=auth.auth_headers())

    result = "PASS" if response.status_code == 200 else "FAIL"

    print("=" * 50)
    print("Read Incident")
    print("(Confirms the integration user can read the Incident data at all - the first basic requirement)")
    print("HTTP Status:", response.status_code)
    print("(200 = read succeeded. Any other code = no read permission or a problem with the sys_id)")
    print("Result:", result)

    record_result("Read Incident", result)


def test_request(auth: ServiceNowOAuthClient, client: httpx.Client, name, method, url, payload=None):
    response = client.request(method, url, headers=auth.auth_headers(), json=payload)

    print("=" * 50)
    print(name)
    print("(Tests creating a new record - such as an AI Execution Log - via POST)")
    print("HTTP Status:", response.status_code)
    print("(201 = created successfully. 403 = blocked by an ACL. 400 = problem with the submitted data)")

    if response.status_code >= 400:
        print("Error details from the server:")
        print(response.text)

    result = "PASS" if response.status_code in (200, 201) else "FAIL"
    record_result(name, result, f"HTTP {response.status_code}")

    return response


def test_allowed_field(auth: ServiceNowOAuthClient, client: httpx.Client, field, value):
    before = get_incident(auth, client).get(field)

    response = update_incident(auth, client, {field: value})

    after = get_incident(auth, client).get(field)

    result = "PASS" if after == value else "FAIL"

    print("=" * 50)
    print(f"Allowed Field: {field}")
    print("(Tries to modify a normal (non-Journal) field and checks whether the value actually changed in the database)")
    print("HTTP Status:", response.status_code)
    print("(200 only means the request was accepted - not enough proof, we must check Before/After)")
    print("Before:", before, "  <- value before the update")
    print("After:", after, "  <- value after the update (if different from Before, the update actually succeeded)")
    print("Result:", result)

    record_result(f"Allowed Field: {field}", result)


def test_allowed_work_notes(auth: ServiceNowOAuthClient, client: httpx.Client):
    """
    work_notes test with extra diagnostics.

    Just knowing After journal count == Before is not enough to know
    the root cause, so we also fetch sys_mod_count from the incident
    itself before and after the request:

    1) sys_mod_count changed but the journal count stayed the same
       => the server accepted the update in general (something on the
       record changed), but specifically work_notes was silently
       ignored. Points to an ACL/permission issue specific to
       work_notes or sys_journal_field itself.

    2) sys_mod_count stayed exactly the same
       => the entire update was ignored with no effect on the record.
       Weaker possibility here since other tests change sys_mod_count
       normally.

    A short delay is inserted after the write and before reading
    sys_journal_field back, because the journal entry may not be
    committed yet at the exact moment the PATCH response returns.
    Without this delay a successful write can be misreported as FAIL.
    """
    before_count = get_journal_count(auth, client, "work_notes")
    before_incident = get_incident(auth, client)
    before_mod_count = before_incident.get("sys_mod_count")

    response = update_incident(auth, client, {"work_notes": "AI verification work note TEST"})

    time.sleep(JOURNAL_WRITE_DELAY_SECONDS)

    after_count = get_journal_count(auth, client, "work_notes")
    after_incident = get_incident(auth, client)
    after_mod_count = after_incident.get("sys_mod_count")

    created = after_count > before_count
    result = "PASS" if created else "FAIL"

    print("=" * 50)
    print("Allowed Field: work_notes")
    print("(work_notes is a Journal field, so it can't be measured by comparing the value like normal fields")
    print(" - the value always comes back empty in the response whether the update succeeded or not")
    print(" - that's why we count records in sys_journal_field before and after, instead of comparing the value)")
    print("HTTP Status:", response.status_code)
    print("(200 here cannot be treated as proof of success - this is the key point in the current issue)")
    print(f"(Waited {JOURNAL_WRITE_DELAY_SECONDS}s after the write before re-reading sys_journal_field)")
    print("Before journal count:", before_count, "  <- number of Journal Entries before the request")
    print("After journal count:", after_count, "  <- number of Journal Entries after the request")
    print("Result:", result, " (PASS requires After > Before, meaning a new record was actually added)")

    print("-" * 50)
    print("Extra diagnostic (sys_mod_count):")
    print("Before sys_mod_count:", before_mod_count)
    print("After sys_mod_count:", after_mod_count)

    if not created:
        try:
            mod_count_changed = int(after_mod_count) != int(before_mod_count)
        except (TypeError, ValueError):
            mod_count_changed = None

        if mod_count_changed is True:
            print(">> Diagnosis: sys_mod_count changed but no Journal Entry was added.")
            print(">> This means the server accepted the update in general (a change was")
            print("   registered on the record), but specifically work_notes was silently ignored.")
            print(">> Strongest likely cause: a permission/ACL specific to work_notes or")
            print("   to the sys_journal_field table itself (not a general incident permission).")
        elif mod_count_changed is False:
            print(">> Diagnosis: sys_mod_count did not change at all.")
            print(">> This means the whole request was ignored with no effect on the record.")
            print(">> This is a weaker possibility here since other fields update fine in other tests.")
        else:
            print(">> Could not determine diagnosis - sys_mod_count value is not a clear number.")

        print("Response (full server response body - useful to confirm there's no hidden error message inside):")
        print(response.text)

    record_result(
        "Allowed Field: work_notes",
        result,
        f"journal count {before_count} -> {after_count}, "
        f"sys_mod_count {before_mod_count} -> {after_mod_count}",
    )


def test_denied_field(auth: ServiceNowOAuthClient, client: httpx.Client, field, attempted_value):
    before = get_incident(auth, client).get(field)

    response = update_incident(auth, client, {field: attempted_value})

    time.sleep(2)  # small settle delay for consistency
    after = get_incident(auth, client).get(field)

    blocked = before == after
    result = "PASS" if blocked else "FAIL"

    print("=" * 50)
    print(f"Denied Field: {field}")
    print("(Tries to modify a field that should be blocked for the integration user, and confirms it was actually blocked)")
    print("HTTP Status:", response.status_code)
    print("(Blocked fields normally still return 200 - ServiceNow silently rejects the update without an error,")
    print(" so HTTP Status alone can't be trusted here - we must compare Before/After)")
    print("Before:", before)
    print("After:", after)
    print("Result:", result, " (PASS means the value did NOT change = the block is working correctly)")

    record_result(f"Denied Field: {field}", result)


def test_denied_comments(auth: ServiceNowOAuthClient, client: httpx.Client):
    before = get_journal_count(auth, client, "comments")

    response = update_incident(auth, client, {"comments": "AI permission verification test"})

    # Same race condition applies here: give it time to (not) commit
    # before checking, so a slow-but-blocked write isn't miscounted.
    time.sleep(JOURNAL_WRITE_DELAY_SECONDS)

    after = get_journal_count(auth, client, "comments")

    blocked = before == after
    result = "PASS" if blocked else "FAIL"

    print("=" * 50)
    print("Denied Field: comments")
    print("(comments is exactly like work_notes - a Journal field, so we count records instead of comparing the value)")
    print("HTTP Status:", response.status_code)
    print(f"(Waited {JOURNAL_WRITE_DELAY_SECONDS}s after the write before re-reading sys_journal_field)")
    print("Before journal count:", before)
    print("After journal count:", after)
    print("Result:", result, " (PASS means the count did NOT change = the block worked and no comment was added)")

    record_result("Denied Field: comments", result)


def run_incident_acl_checks(auth: ServiceNowOAuthClient):
    print()
    print("=" * 50)
    print("PART B: Incident field-level ACL verification")
    print("=" * 50)

    with httpx.Client(timeout=15) as client:
        # Allowed -- everything the integration user is expected to be
        # able to do. Any failure here means a missing permission.
        test_read_incident(auth, client)

        test_request(
            auth, client,
            "Create AI Execution Log",
            "POST",
            EXECUTION_LOG_URL,
            {
                "execution_id": "test-execution-001",
                "action": "permission_test",
                "agent": "AI Incident Agent",
                "status": "started",
                "result": "Verification test",
            },
        )

        test_allowed_field(auth, client, "x_2215689_ai_inc_0_u_ai_processing_state", "complete")

        test_allowed_work_notes(auth, client)
        get_journal_entries(auth, client, "work_notes")

        # Denied -- everything the integration user must NOT be able
        # to do. Any failure here means an excessive/dangerous
        # permission that must be removed from the ACL or the role.
        test_denied_field(auth, client, "priority", "1")
        test_denied_field(auth, client, "state", "2")
        test_denied_field(auth, client, "assigned_to", "681ccaf9c0a8016400b98a06818d57c7")
        test_denied_field(auth, client, "assignment_group", "287ebd7da9fe198100f92cc8d1d2154e")
        test_denied_field(auth, client, "x_2215689_ai_inc_0_human_lock", "true")
        test_denied_comments(auth, client)


# ---------------------------------------------------------------------------
# Summary + entrypoint
# ---------------------------------------------------------------------------

def print_summary():
    print("\n")
    print("#" * 50)
    print("Final Summary of All Tests")
    print("#" * 50)

    passed = [r for r in RESULTS if r[1] == "PASS"]
    failed = [r for r in RESULTS if r[1] == "FAIL"]

    for name, status, note in RESULTS:
        line = f"[{status}] {name}"
        if note:
            line += f"   ({note})"
        print(line)

    print("-" * 50)
    print(f"Total tests: {len(RESULTS)}")
    print(f"Passed: {len(passed)}")
    print(f"Failed: {len(failed)}")

    print("-" * 50)
    if not failed:
        print("All tests passed - permissions are configured exactly as required.")
    else:
        print("There is a problem with:")
        for name, status, note in failed:
            print(f"  - {name}")
        print(
            "\nNote: review the 'Extra diagnostic' section printed above for each "
            "failed test before changing any ACL - each diagnosis points to whether "
            "the likely cause is the ACL, the payload, or something else."
        )

=======
>>>>>>> origin/s2.1/fast-api-webhook
    print("\nThis output -- not the ACL configuration screen -- is the evidence "
          "of what this identity can actually do.")


<<<<<<< HEAD
def main():
    print("Starting ServiceNow OAuth Identity Permission Verification")
    print("=" * 50)

    auth = ServiceNowOAuthClient()

    if not run_identity_capability_checks(auth):
        print_summary()
        sys.exit(1)

    run_incident_acl_checks(auth)

    print_summary()


=======
>>>>>>> origin/s2.1/fast-api-webhook
if __name__ == "__main__":
    main()
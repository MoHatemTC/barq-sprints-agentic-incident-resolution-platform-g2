import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


# ============================================================
# LOAD CONFIGURATION
# ============================================================

load_dotenv()

INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE", "").rstrip("/")
USERNAME = os.getenv("SERVICENOW_USERNAME")
PASSWORD = os.getenv("SERVICENOW_PASSWORD")

CLIENT_ID = os.getenv("SERVICENOW_CLIENT_ID")
CLIENT_SECRET = os.getenv("SERVICENOW_CLIENT_SECRET")

INCIDENT_SYS_ID = os.getenv("INCIDENT_SYS_ID")


# ============================================================
# CORRECT SERVICENOW SCOPE / TABLE / FIELD NAMES
# ============================================================

APP_SCOPE = "x_2216057_ai_inc_0"

EXECUTION_LOG_TABLE = f"{APP_SCOPE}_ai_execution_log"

AI_PROCESSING_STATE = f"{APP_SCOPE}_ai_processing_state"
AI_CLASSIFICATION = f"{APP_SCOPE}_ai_classification"
AI_CONFIDENCE = f"{APP_SCOPE}_ai_confidence"
AI_SUGGESTION = f"{APP_SCOPE}_ai_suggestion"
AI_RESOLUTION = f"{APP_SCOPE}_ai_resolution"
AI_FAILURE_REASON = f"{APP_SCOPE}_ai_failure_reason"
AI_MODEL_NAME = f"{APP_SCOPE}_ai_model_name"
AI_AGENT_VERSION = f"{APP_SCOPE}_ai_agent_version"
AI_PROCESSING_STARTED_AT = f"{APP_SCOPE}_ai_processing_started_at"
AI_PROCESSING_ENDED_AT = f"{APP_SCOPE}_ai_processing_ended_at"
AI_HUMAN_REVIEW_REQUIRED = f"{APP_SCOPE}_ai_human_review_required"
AI_HUMAN_LOCK = f"{APP_SCOPE}_ai_human_lock"


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

required_env = {
    "SERVICENOW_INSTANCE": INSTANCE_URL,
    "SERVICENOW_USERNAME": USERNAME,
    "SERVICENOW_PASSWORD": PASSWORD,
    "SERVICENOW_CLIENT_ID": CLIENT_ID,
    "SERVICENOW_CLIENT_SECRET": CLIENT_SECRET,
    "INCIDENT_SYS_ID": INCIDENT_SYS_ID,
}

missing = [name for name, value in required_env.items() if not value]

if missing:
    print("Missing required environment variables:")
    for name in missing:
        print(f"  - {name}")
    sys.exit(1)


# ============================================================
# OAUTH
# ============================================================

def get_access_token():
    print("Requesting OAuth access token...")

    response = requests.post(
        f"{INSTANCE_URL}/oauth_token.do",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": USERNAME,
            "password": PASSWORD,
        },
        timeout=30,
    )

    if response.status_code != 200:
        print("OAuth authentication failed")
        print("HTTP Status:", response.status_code)
        print(response.text)
        sys.exit(1)

    payload = response.json()

    if "access_token" not in payload:
        print("OAuth response did not contain access_token")
        print(payload)
        sys.exit(1)

    print("OAuth authentication: PASS")
    return payload["access_token"]


ACCESS_TOKEN = get_access_token()

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ============================================================
# URLS
# ============================================================

INCIDENT_URL = (
    f"{INSTANCE_URL}/api/now/table/incident/"
    f"{INCIDENT_SYS_ID}"
)

EXECUTION_LOG_URL = (
    f"{INSTANCE_URL}/api/now/table/"
    f"{EXECUTION_LOG_TABLE}"
)


# ============================================================
# TEST RESULT TRACKING
# ============================================================

RESULTS = []


def record_result(name, passed, detail=""):
    RESULTS.append(
        {
            "name": name,
            "passed": passed,
            "detail": detail,
        }
    )


def print_separator():
    print("=" * 70)


# ============================================================
# HELPERS
# ============================================================

def extract_value(value):
    """
    ServiceNow references may be returned as:
        {"link": "...", "value": "sys_id"}

    Normal fields are returned directly.
    """
    if isinstance(value, dict):
        return value.get("value")

    return value


def get_incident():
    response = requests.get(
        INCIDENT_URL,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code != 200:
        print("Failed reading Incident")
        print("HTTP Status:", response.status_code)
        print(response.text)
        return None

    return response.json()["result"]


def update_incident(payload):
    return requests.patch(
        INCIDENT_URL,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )


def get_journal_count(element):
    """
    Query journal entries belonging to the Incident.
    """

    url = (
        f"{INSTANCE_URL}/api/now/table/sys_journal_field"
        f"?sysparm_query="
        f"element={element}"
        f"^element_id={INCIDENT_SYS_ID}"
        f"&sysparm_limit=100"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code != 200:
        print(f"Unable to read journal entries for {element}")
        print("HTTP Status:", response.status_code)
        print(response.text)
        return None

    return len(response.json().get("result", []))


# ============================================================
# TEST 1 — INCIDENT READ
# ============================================================

def test_read_incident():
    print_separator()
    print("TEST: Read Incident")

    response = requests.get(
        INCIDENT_URL,
        headers=HEADERS,
        timeout=30,
    )

    passed = response.status_code == 200

    print("HTTP Status:", response.status_code)
    print("Result:", "PASS" if passed else "FAIL")

    if not passed:
        print(response.text)

    record_result(
        "Read Incident",
        passed,
        f"HTTP {response.status_code}",
    )

    return passed


# ============================================================
# TEST 2 — CREATE AI EXECUTION LOG
# ============================================================

def test_create_execution_log():
    print_separator()
    print("TEST: Create AI Execution Log")

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    payload = {
        "incident": INCIDENT_SYS_ID,
        "execution_id": (
            "permission-test-"
            + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        ),
        "action": "orchestrator.permission_test",
        "agent": "barq-agent-1.0.0",
        "timestamp": timestamp,
        "status": "started",
        "result": "Sprint 1 ACL verification test",
        "error": "",
    }

    response = requests.post(
        EXECUTION_LOG_URL,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )

    passed = response.status_code in (200, 201)

    print("Table:", EXECUTION_LOG_TABLE)
    print("HTTP Status:", response.status_code)
    print("Result:", "PASS" if passed else "FAIL")

    if not passed:
        print(response.text)

    record_result(
        "Create AI Execution Log",
        passed,
        f"HTTP {response.status_code}",
    )


# ============================================================
# GENERIC ALLOWED FIELD TEST
# ============================================================

def test_allowed_field(field, value):
    print_separator()
    print(f"TEST: Allowed Field -> {field}")

    before_record = get_incident()

    if before_record is None:
        record_result(
            f"Allowed {field}",
            False,
            "Could not read Incident before update",
        )
        return

    before = extract_value(before_record.get(field))

    response = update_incident(
        {
            field: value,
        }
    )

    after_record = get_incident()

    if after_record is None:
        record_result(
            f"Allowed {field}",
            False,
            "Could not read Incident after update",
        )
        return

    after = extract_value(after_record.get(field))

    passed = str(after) == str(value)

    print("HTTP Status:", response.status_code)
    print("Before:", before)
    print("Requested:", value)
    print("After:", after)
    print("Result:", "PASS" if passed else "FAIL")

    if not passed:
        print("PATCH response:")
        print(response.text)

    record_result(
        f"Allowed {field}",
        passed,
        f"before={before}, requested={value}, after={after}",
    )


# ============================================================
# WORK NOTES TEST
# ============================================================

def test_allowed_work_notes():
    print_separator()
    print("TEST: Allowed Field -> work_notes")

    before = get_journal_count("work_notes")

    if before is None:
        print(
            "Could not verify journal count. "
            "Test is inconclusive."
        )

        record_result(
            "Allowed work_notes",
            False,
            "Unable to read sys_journal_field",
        )
        return

    unique_note = (
        "BARQ AI permission verification "
        + datetime.now(timezone.utc).isoformat()
    )

    response = update_incident(
        {
            "work_notes": unique_note,
        }
    )

    after = get_journal_count("work_notes")

    if after is None:
        record_result(
            "Allowed work_notes",
            False,
            "Unable to read journal after PATCH",
        )
        return

    created = after > before

    print("HTTP Status:", response.status_code)
    print("Before journal count:", before)
    print("After journal count:", after)
    print("Result:", "PASS" if created else "FAIL")

    if not created:
        print("PATCH response:")
        print(response.text)

    record_result(
        "Allowed work_notes",
        created,
        f"journal count {before} -> {after}",
    )


# ============================================================
# GENERIC DENIED FIELD TEST
# ============================================================

def test_denied_field(field, attempted_value):
    print_separator()
    print(f"TEST: Denied Field -> {field}")

    before_record = get_incident()

    if before_record is None:
        record_result(
            f"Denied {field}",
            False,
            "Unable to read Incident before test",
        )
        return

    before = extract_value(before_record.get(field))

    # Prevent false PASS if test value already equals current value.
    if str(before).lower() == str(attempted_value).lower():
        print("SKIP")
        print(
            "Attempted value equals current value, "
            "so this would not prove the ACL."
        )

        record_result(
            f"Denied {field}",
            False,
            "Test value equals current value",
        )
        return

    response = update_incident(
        {
            field: attempted_value,
        }
    )

    after_record = get_incident()

    if after_record is None:
        record_result(
            f"Denied {field}",
            False,
            "Unable to read Incident after test",
        )
        return

    after = extract_value(after_record.get(field))

    blocked = str(before).lower() == str(after).lower()

    print("HTTP Status:", response.status_code)
    print("Before:", before)
    print("Attempted:", attempted_value)
    print("After:", after)
    print("Result:", "PASS" if blocked else "FAIL")

    if not blocked:
        print(
            "WARNING: The protected field changed. "
            "The deny ACL may not be working."
        )

    record_result(
        f"Denied {field}",
        blocked,
        f"before={before}, attempted={attempted_value}, after={after}",
    )


# ============================================================
# COMMENTS TEST
# ============================================================

def test_denied_comments():
    print_separator()
    print("TEST: Denied Field -> comments")

    before = get_journal_count("comments")

    if before is None:
        record_result(
            "Denied comments",
            False,
            "Unable to read comments journal",
        )
        return

    response = update_incident(
        {
            "comments": (
                "BARQ AI denied-comments verification "
                + datetime.now(timezone.utc).isoformat()
            )
        }
    )

    after = get_journal_count("comments")

    if after is None:
        record_result(
            "Denied comments",
            False,
            "Unable to read journal after test",
        )
        return

    blocked = before == after

    print("HTTP Status:", response.status_code)
    print("Before journal count:", before)
    print("After journal count:", after)
    print("Result:", "PASS" if blocked else "FAIL")

    if not blocked:
        print(
            "WARNING: Comment journal entry was created. "
            "Comments ACL did not block the integration."
        )

    record_result(
        "Denied comments",
        blocked,
        f"journal count {before} -> {after}",
    )


# ============================================================
# DYNAMIC DENIED TESTS
# ============================================================

def test_denied_priority():
    incident = get_incident()

    if incident is None:
        return

    current = str(
        extract_value(
            incident.get("priority")
        )
    )

    # Choose a different valid priority automatically.
    attempted = "2" if current != "2" else "3"

    test_denied_field(
        "priority",
        attempted,
    )


def test_denied_state():
    incident = get_incident()

    if incident is None:
        return

    current = str(
        extract_value(
            incident.get("state")
        )
    )

    attempted = "2" if current != "2" else "3"

    test_denied_field(
        "state",
        attempted,
    )


def test_denied_assigned_to():
    incident = get_incident()

    if incident is None:
        return

    current = extract_value(
        incident.get("assigned_to")
    )

    # Clearing the value is enough to prove denial,
    # provided it currently has a value.
    if current:
        attempted = ""
    else:
        print_separator()
        print("TEST: Denied Field -> assigned_to")
        print(
            "SKIP: assigned_to is already empty. "
            "Provide an alternate sys_user sys_id if needed."
        )

        record_result(
            "Denied assigned_to",
            False,
            "Current value already empty",
        )
        return

    test_denied_field(
        "assigned_to",
        attempted,
    )


def test_denied_assignment_group():
    incident = get_incident()

    if incident is None:
        return

    current = extract_value(
        incident.get("assignment_group")
    )

    if current:
        attempted = ""
    else:
        print_separator()
        print("TEST: Denied Field -> assignment_group")
        print(
            "SKIP: assignment_group is already empty. "
            "Provide an alternate group sys_id if needed."
        )

        record_result(
            "Denied assignment_group",
            False,
            "Current value already empty",
        )
        return

    test_denied_field(
        "assignment_group",
        attempted,
    )


def test_denied_human_lock():
    incident = get_incident()

    if incident is None:
        return

    current = str(
        extract_value(
            incident.get(AI_HUMAN_LOCK)
        )
    ).lower()

    attempted = (
        "false"
        if current == "true"
        else "true"
    )

    test_denied_field(
        AI_HUMAN_LOCK,
        attempted,
    )


# ============================================================
# SUMMARY
# ============================================================

def print_summary():
    print()
    print("=" * 70)
    print("SPRINT 1 ACL VERIFICATION SUMMARY")
    print("=" * 70)

    passed = 0
    failed = 0

    for result in RESULTS:
        status = "PASS" if result["passed"] else "FAIL"

        print(
            f"{status:4} | "
            f"{result['name']}"
        )

        if result["passed"]:
            passed += 1
        else:
            failed += 1

    print("=" * 70)
    print("Passed:", passed)
    print("Failed:", failed)

    if failed == 0:
        print("OVERALL RESULT: PASS")
    else:
        print("OVERALL RESULT: ATTENTION REQUIRED")


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("Starting Sprint 1 ACL Verification")
    print("Instance:", INSTANCE_URL)
    print("User:", USERNAME)
    print("Incident:", INCIDENT_SYS_ID)
    print("Scope:", APP_SCOPE)

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    if not test_read_incident():
        print()
        print(
            "Incident read failed. "
            "Stopping remaining tests."
        )
        print_summary()
        return

    # --------------------------------------------------------
    # EXECUTION LOG
    # --------------------------------------------------------

    test_create_execution_log()

    # --------------------------------------------------------
    # ALLOWED INCIDENT FIELDS
    # --------------------------------------------------------

    test_allowed_field(
        AI_PROCESSING_STATE,
        "complete",
    )

    test_allowed_field(
        AI_CLASSIFICATION,
        "permission-test-classification",
    )

    test_allowed_field(
        AI_MODEL_NAME,
        "permission-test-model",
    )

    test_allowed_field(
        AI_AGENT_VERSION,
        "barq-agent-permission-test",
    )

    test_allowed_field(
        AI_HUMAN_REVIEW_REQUIRED,
        "true",
    )

    test_allowed_work_notes()

    # --------------------------------------------------------
    # DENIED FIELDS
    # --------------------------------------------------------

    test_denied_priority()

    test_denied_state()

    test_denied_assigned_to()

    test_denied_assignment_group()

    test_denied_human_lock()

    test_denied_comments()

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print_summary()


if __name__ == "__main__":
    main()
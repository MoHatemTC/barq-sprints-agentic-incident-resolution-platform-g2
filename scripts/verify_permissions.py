import os
import requests
import uuid
from dotenv import load_dotenv


# ============================================================
# Load configuration
# ============================================================

load_dotenv()

INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE", "").rstrip("/")

USERNAME = os.getenv("SERVICENOW_USERNAME")
PASSWORD = os.getenv("SERVICENOW_PASSWORD")

CLIENT_ID = os.getenv("SERVICENOW_CLIENT_ID")
CLIENT_SECRET = os.getenv("SERVICENOW_CLIENT_SECRET")

INCIDENT_SYS_ID = os.getenv("INCIDENT_SYS_ID")


# ============================================================
# ServiceNow URLs
# ============================================================

INCIDENT_URL = (
    f"{INSTANCE_URL}/api/now/table/incident/"
    f"{INCIDENT_SYS_ID}"
)

EXECUTION_LOG_URL = (
    f"{INSTANCE_URL}/api/now/table/"
    "x_2215689_ai_inc_0_ai_execution_log"
)

JOURNAL_URL = (
    f"{INSTANCE_URL}/api/now/table/sys_journal_field"
)


# ============================================================
# OAuth
# ============================================================

def get_access_token():
    """
    Authenticate the integration user using OAuth.
    """

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
        print("=" * 50)
        print("OAuth Authentication")
        print("HTTP Status:", response.status_code)
        print("Result: FAIL")
        print(response.text)
        exit(1)

    print("=" * 50)
    print("OAuth Authentication")
    print("HTTP Status:", response.status_code)
    print("Result: PASS")

    return response.json()["access_token"]


ACCESS_TOKEN = get_access_token()


HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ============================================================
# Incident functions
# ============================================================

def get_incident():
    """
    Read the current incident.
    """

    response = requests.get(
        INCIDENT_URL,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code != 200:
        print("Failed to read incident")
        print(response.text)
        exit(1)

    return response.json()["result"]


def update_incident(payload):
    """
    Update the incident through Table API.
    """

    return requests.patch(
        INCIDENT_URL,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )


# ============================================================
# Journal functions
# ============================================================

def get_journal_entries(element):
    """
    Retrieve journal entries for the current incident.

    Used for:
        work_notes
        comments
    """

    params = {
        "sysparm_query": (
            f"name=incident"
            f"^element={element}"
            f"^element_id={INCIDENT_SYS_ID}"
        ),
        "sysparm_fields": (
            "sys_id,name,element,element_id,"
            "value,sys_created_by,sys_created_on"
        ),
        "sysparm_limit": "100",
    }

    response = requests.get(
        JOURNAL_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    if response.status_code != 200:
        print("Failed to read sys_journal_field")
        print(response.text)
        exit(1)

    return response.json()["result"]


# ============================================================
# Generic request test
# ============================================================

def test_request(name, method, url, payload=None):
    """
    Execute a generic ServiceNow API request.
    """

    response = requests.request(
        method,
        url,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )

    print("=" * 50)
    print(name)
    print("HTTP Status:", response.status_code)

    if response.status_code >= 400:
        print(response.text)

    return response


# ============================================================
# TEST 1
# Incident READ
# ============================================================

def test_read_incident():
    """
    Sprint requirement:
    Integration identity must be able to read incidents.
    """

    response = requests.get(
        INCIDENT_URL,
        headers=HEADERS,
        timeout=30,
    )

    passed = response.status_code == 200

    print("=" * 50)
    print("Read Incident")
    print("HTTP Status:", response.status_code)
    print("Result:", "PASS" if passed else "FAIL")

    if not passed:
        print(response.text)

    return passed


# ============================================================
# TEST 2
# AI Execution Log CREATE
# ============================================================

def test_execution_log():
    """
    Sprint requirement:
    Integration identity must be able to create
    an AI Execution Log record.
    """

    payload = {
        "execution_id": "test-execution-001",
        "action": "permission_test",
        "agent": "AI Incident Agent",
        "status": "started",
        "result": "Sprint 1 permission verification",
    }

    response = test_request(
        "Create AI Execution Log",
        "POST",
        EXECUTION_LOG_URL,
        payload,
    )

    passed = response.status_code == 201

    print(
        "Result:",
        "PASS" if passed else "FAIL"
    )

    return passed


# ============================================================
# TEST 3
# Allowed AI field
# ============================================================

def test_allowed_ai_field():
    """
    Sprint requirement:
    Integration identity may write AI fields.
    """

    field = (
        "x_2215689_ai_inc_0_u_ai_processing_state"
    )

    before = get_incident().get(field)

    test_value = "complete"

    response = update_incident({
        field: test_value
    })

    after = get_incident().get(field)

    passed = (
        response.status_code == 200
        and after == test_value
    )

    print("=" * 50)
    print("Allowed Field:", field)
    print("HTTP Status:", response.status_code)
    print("Before:", before)
    print("After:", after)
    print("Result:", "PASS" if passed else "FAIL")

    return passed


# ============================================================
# TEST 4
# Allowed work_notes
# ============================================================

def test_allowed_work_notes():
    """
    Sprint requirement:
    Integration identity may write work_notes.

    IMPORTANT:
    work_notes is a journal field.
    Therefore we verify the actual sys_journal_field
    record instead of trusting HTTP 200.
    """

    before_entries = get_journal_entries(
        "work_notes"
    )

    before_count = len(before_entries)

    test_note = (
        "AI permission verification work note"
    )

    response = update_incident({
        "work_notes": test_note
    })

    after_entries = get_journal_entries(
        "work_notes"
    )

    after_count = len(after_entries)

    journal_created = after_count > before_count

    passed = (
        response.status_code == 200
        and journal_created
    )

    print("=" * 50)
    print("Allowed Field: work_notes")
    print("HTTP Status:", response.status_code)
    print("Before journal count:", before_count)
    print("After journal count:", after_count)
    print("Result:", "PASS" if passed else "FAIL")

    if not passed:
        print(
            "Response:",
            response.text
        )

    return passed


# ============================================================
# Generic DENIED field test
# ============================================================

def test_denied_field(field, attempted_value):
    """
    Verify that a restricted field cannot change.

    HTTP 200 alone is NOT considered success.
    We compare the actual value before and after.
    """

    before = get_incident().get(field)

    response = update_incident({
        field: attempted_value
    })

    after = get_incident().get(field)

    blocked = before == after

    print("=" * 50)
    print("Denied Field:", field)
    print("HTTP Status:", response.status_code)
    print("Before:", before)
    print("After:", after)
    print("Result:", "PASS" if blocked else "FAIL")

    if not blocked:
        print(
            "WARNING:",
            field,
            "was changed by the integration identity."
        )

    return blocked


# ============================================================
# TEST 5
# Deny priority
# ============================================================

def test_priority():
    return test_denied_field(
        "priority",
        "1"
    )


# ============================================================
# TEST 6
# Deny state
# ============================================================

def test_state():
    return test_denied_field(
        "state",
        "2"
    )


# ============================================================
# TEST 7
# Deny assigned_to
# ============================================================

def test_assigned_to():
    return test_denied_field(
        "assigned_to",
        "681ccaf9c0a8016400b98a06818d57c7"
    )


# ============================================================
# TEST 8
# Deny assignment_group
# ============================================================

def test_assignment_group():
    return test_denied_field(
        "assignment_group",
        "287ebd7da9fe198100f92cc8d1d2154e"
    )


# ============================================================
# TEST 9
# Deny human_lock
# ============================================================

def test_human_lock():
    return test_denied_field(
        "x_2215689_ai_inc_0_human_lock",
        "true"
    )


# ============================================================
# TEST 10
# Deny comments
# ============================================================

def test_denied_comments():
    """
    Verify that the integration user cannot create comments.

    We use a unique marker and search for that exact value
    instead of relying only on journal record counts.
    """

    comment_marker = (
        "AI_PERMISSION_TEST_COMMENT_"
        + uuid.uuid4().hex[:8]
    )

    response = update_incident({
        "comments": comment_marker
    })

    params = {
        "sysparm_query": (
            f"name=incident"
            f"^element=comments"
            f"^element_id={INCIDENT_SYS_ID}"
            f"^value={comment_marker}"
        ),
        "sysparm_fields": (
            "sys_id,value,element,element_id,"
            "sys_created_by,sys_created_on"
        ),
        "sysparm_limit": "10",
    }

    journal_response = requests.get(
        JOURNAL_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    if journal_response.status_code != 200:
        print("=" * 50)
        print("Denied Field: comments")
        print(
            "Journal lookup HTTP Status:",
            journal_response.status_code
        )
        print("Result: FAIL")
        print(journal_response.text)
        return False

    entries = journal_response.json()["result"]

    blocked = len(entries) == 0

    print("=" * 50)
    print("Denied Field: comments")
    print("PATCH HTTP Status:", response.status_code)
    print("Test marker:", comment_marker)
    print("Matching journal entries:", len(entries))
    print("Result:", "PASS" if blocked else "FAIL")

    if entries:
        print("WARNING: Integration user created a comment!")
        print(entries)

    return blocked

# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 50)
    print("Starting Sprint 1 ACL Verification")
    print("=" * 50)

    # --------------------------------------------------------
    # ALLOWED
    # --------------------------------------------------------

    test_read_incident()

    test_execution_log()

    test_allowed_ai_field()

    test_allowed_work_notes()

    # --------------------------------------------------------
    # DENIED
    # --------------------------------------------------------

    test_priority()

    test_state()

    test_assigned_to()

    test_assignment_group()

    test_human_lock()

    test_denied_comments()

    # --------------------------------------------------------
    # END
    # --------------------------------------------------------

    print()
    print("=" * 50)
    print("Verification Complete")
    print("=" * 50)


if __name__ == "__main__":
    main()
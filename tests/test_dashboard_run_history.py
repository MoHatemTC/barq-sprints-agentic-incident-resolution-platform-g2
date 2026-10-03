"""Dashboard run history: every past run of an incident, with its logs.

Runs against the real database inside one transaction that is rolled back,
so nothing is left behind (approval rows cannot be deleted by design).
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.routers import dashboard
from src.db.database import engine
from src.db.models import Approval, Execution, Failure, RetryState, WorkflowState

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    session.close = lambda: None  # the endpoints close their session; keep ours open
    monkeypatch.setattr(dashboard, "SessionLocal", lambda: session)
    try:
        yield session
    finally:
        Session.close(session)
        outer.rollback()
        connection.close()


@pytest.fixture
def api(db):
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def _run(db, number, minutes_ago, status="succeeded", **kwargs):
    eid = f"hist-{uuid4().hex}"
    started = NOW - timedelta(minutes=minutes_ago)
    db.add(Execution(execution_identifier=eid, incident_reference=number, status=status,
                     started_at=started, ended_at=started + timedelta(seconds=42), **kwargs))
    db.flush()
    return eid


@pytest.fixture
def incident(db):
    """Three runs: failed, then paused + approved + resumed, then a plain success."""
    number = f"INC-HIST-{uuid4().hex[:8]}"
    failed = _run(db, number, 30, status="failed", node_reached="act")
    approved = _run(db, number, 20, model_name="gpt-x", agent_version="s4")
    latest = _run(db, number, 10)
    db.add_all([
        Failure(execution_reference=failed, failing_node="act", error_class="ServiceNowNetworkError",
                message="timed out", retry_count=3),
        RetryState(execution_reference=failed, attempt_count=3),
        WorkflowState(execution_reference=approved, node_name="interrupt",
                      checkpoint=json.dumps({"gate": "high_risk", "risk": "high"}),
                      created_at=NOW - timedelta(minutes=19)),
        WorkflowState(execution_reference=approved, node_name="resume:human",
                      checkpoint=json.dumps({"action_taken": "resolved_with_human_solution"}),
                      created_at=NOW - timedelta(minutes=15)),
        Approval(execution_reference=approved, evidence_presented="brief", reviewer_decision="approved",
                 reviewer_identity="hady", human_solution="Restart the payroll DB",
                 decision_timestamp=NOW - timedelta(minutes=16)),
        WorkflowState(execution_reference=latest, node_name="result",
                      checkpoint=json.dumps({"action_taken": "resolved_automatically"}),
                      created_at=NOW - timedelta(minutes=9)),
    ])
    db.flush()
    return number, failed, approved, latest


def test_history_lists_every_run_newest_first_with_its_logs(api, incident):
    number, failed, approved, latest = incident

    body = api.get(f"/api/v1/dashboard/incidents/{number}/runs").json()

    assert [r["execution_id"] for r in body["runs"]] == [latest, approved, failed]
    assert body["next_before"] is None
    old = body["runs"][2]
    assert old["status"] == "failed" and old["node_reached"] == "act"
    assert old["failures"] == [{"failing_node": "act", "error_class": "ServiceNowNetworkError",
                                "message": "timed out", "retry_count": 3}]
    assert old["retry_attempt_count"] == 3 and old["duration_seconds"] == 42
    paused = body["runs"][1]
    assert [e["node_name"] for e in paused["log"]] == ["interrupt", "resume:human"]
    assert paused["approvals"] == [{"decision": "approved", "reviewer": "hady",
                                    "decided_at": "2026-10-01T11:44:00+00:00",
                                    "human_solution": "Restart the payroll DB"}]
    assert paused["model_name"] == "gpt-x"


def test_history_lists_logs_with_their_size_but_not_their_payloads(api, incident):
    number = incident[0]

    body = api.get(f"/api/v1/dashboard/incidents/{number}/runs").json()

    entry = body["runs"][1]["log"][0]
    assert set(entry) == {"entry_id", "node_name", "created_at", "size_bytes"}
    assert entry["size_bytes"] == len(json.dumps({"gate": "high_risk", "risk": "high"}))


def test_one_log_payload_is_fetched_on_demand(api, incident):
    number, _, approved, _ = incident
    entry = api.get(f"/api/v1/dashboard/incidents/{number}/runs").json()["runs"][1]["log"][0]

    body = api.get(f"/api/v1/dashboard/runs/{approved}/log/{entry['entry_id']}").json()

    assert body["node_name"] == "interrupt"
    assert body["payload"] == {"gate": "high_risk", "risk": "high"}
    assert body["truncated"] is False and body["preview"] is None


def _big_entry(db, eid, size):
    text = json.dumps({"diagnosis": "x" * size})
    entry = WorkflowState(execution_reference=eid, node_name="result", checkpoint=text, created_at=NOW)
    db.add(entry)
    db.flush()
    return entry.id, text


def test_large_log_opens_as_a_preview(api, incident, db, monkeypatch):
    monkeypatch.setattr(dashboard, "LOG_PREVIEW_BYTES", 64)
    _, _, _, latest = incident
    entry_id, text = _big_entry(db, latest, 500)

    body = api.get(f"/api/v1/dashboard/runs/{latest}/log/{entry_id}").json()

    assert body["truncated"] is True and body["payload"] is None
    assert body["preview"] == text[:64]          # only the slice crosses the wire
    assert body["size_bytes"] == len(text)


def test_full_log_is_loaded_on_request(api, incident, db, monkeypatch):
    monkeypatch.setattr(dashboard, "LOG_PREVIEW_BYTES", 64)
    _, _, _, latest = incident
    entry_id, text = _big_entry(db, latest, 500)

    body = api.get(f"/api/v1/dashboard/runs/{latest}/log/{entry_id}?full=true").json()

    assert body["truncated"] is False and body["preview"] is None
    assert body["payload"] == json.loads(text)


def test_log_at_the_preview_limit_opens_in_full(api, incident, db, monkeypatch):
    _, _, _, latest = incident
    entry_id, text = _big_entry(db, latest, 10)
    monkeypatch.setattr(dashboard, "LOG_PREVIEW_BYTES", len(text))

    body = api.get(f"/api/v1/dashboard/runs/{latest}/log/{entry_id}").json()

    assert body["truncated"] is False and body["payload"] == json.loads(text)


def test_log_entry_of_another_run_is_not_found(api, incident):
    number, failed, _, _ = incident
    entry = api.get(f"/api/v1/dashboard/incidents/{number}/runs").json()["runs"][1]["log"][0]

    assert api.get(f"/api/v1/dashboard/runs/{failed}/log/{entry['entry_id']}").status_code == 404
    assert api.get(f"/api/v1/dashboard/runs/{failed}/log/999999999").status_code == 404


def test_older_runs_come_page_by_page_without_gaps_or_repeats(api, db):
    number = f"INC-HIST-{uuid4().hex[:8]}"
    made = [_run(db, number, minutes) for minutes in range(1, 24)]  # 23 runs, newest first
    same_start = _run(db, number, 5)  # same start as made[4]: the newer row comes first
    expected = made[:4] + [same_start, made[4]] + made[5:]

    seen, before = [], None
    while True:
        url = f"/api/v1/dashboard/incidents/{number}/runs?limit=10" + (f"&before={before}" if before else "")
        page = api.get(url).json()
        seen += [r["execution_id"] for r in page["runs"]]
        before = page["next_before"]
        if before is None:
            break

    assert seen == expected  # 10 + 10 + 4, nothing skipped or repeated across pages


def test_unknown_incident_has_no_runs(api):
    body = api.get("/api/v1/dashboard/incidents/INC-NOPE/runs").json()

    assert body == {"incident_number": "INC-NOPE", "runs": [], "next_before": None}


def test_cursor_from_another_incident_is_rejected(api, incident, db):
    other = _run(db, "INC-OTHER-" + uuid4().hex[:6], 1)
    other_id = db.query(Execution.id).filter(Execution.execution_identifier == other).scalar()

    response = api.get(f"/api/v1/dashboard/incidents/{incident[0]}/runs?before={other_id}")

    assert response.status_code == 400


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "before=0"])
def test_page_bounds_are_validated(api, query):
    assert api.get(f"/api/v1/dashboard/incidents/INC1/runs?{query}").status_code == 422

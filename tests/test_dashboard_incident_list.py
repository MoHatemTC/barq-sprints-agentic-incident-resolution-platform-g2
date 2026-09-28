"""Dashboard incident list: ServiceNow is the source of truth.

Every incident in ServiceNow is listed (not only the ones the AI processed),
each with its latest AI run from Postgres. ServiceNow is read through a short
shared cache so dashboard polling never competes with the AI worker.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers import dashboard
from src.db.database import SessionLocal
from src.db.models import Execution, Failure, RetryState, WorkflowState


def _cell(value, display=None):
    return {"value": value, "display_value": value if display is None else display}


def _sn_row(number, sys_id, *, category="network", human_lock="false", ai_enabled="true"):
    fields = dashboard._incident_fields()
    return {
        fields["sys_id"]: _cell(sys_id),
        fields["number"]: _cell(number),
        fields["short_description"]: _cell(f"{number} problem"),
        fields["category"]: _cell(category, category.title()),
        fields["state"]: _cell("1", "New"),
        fields["priority"]: _cell("4", "4 - Low"),
        fields["created_at"]: _cell("2026-09-29 08:30:00", "29/09/2026 11:30:00"),
        fields["ai_processing_state"]: _cell("pending", "Pending"),
        fields["human_lock"]: _cell(human_lock),
        fields["ai_enabled"]: _cell(ai_enabled),
    }


class FakeServiceNow:
    def __init__(self, rows):
        self.rows, self.calls, self.error = rows, [], None

    def list_incidents(self, fields, limit=20, offset=0):
        self.calls.append((limit, offset))
        if self.error:
            raise self.error
        return self.rows[offset:offset + limit], len(self.rows)


RUN = {"execution_id": "run-1", "status": "succeeded"}


@pytest.fixture
def api(monkeypatch):
    fake = FakeServiceNow([
        _sn_row("INC0010002", "sys-2"),
        _sn_row("INC0010001", "sys-1", category="inquiry", human_lock="true"),
    ])
    monkeypatch.setattr(dashboard, "_servicenow", lambda: fake)
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=60))
    monkeypatch.setattr(dashboard, "_latest_runs", lambda db, numbers: {"INC0010002": RUN})
    monkeypatch.setattr(dashboard, "SessionLocal", lambda: type("DB", (), {"close": lambda self: None})())
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app), fake


def test_lists_every_servicenow_incident_with_its_ai_run(api):
    client, _ = api

    body = client.get("/api/v1/dashboard/incidents?limit=20").json()

    assert body["total"] == 2 and body["stale"] is False
    processed, not_sent = body["incidents"]
    assert processed["number"] == "INC0010002"
    assert processed["execution"] == RUN
    assert processed["category"] == "Network"          # display label, like the form
    assert processed["state"] == "New"
    assert processed["created_at"] == "2026-09-29T08:30:00+00:00"  # UTC value, not the display string
    assert not_sent["execution"] is None                # skipped by the Business Rule, still listed
    assert not_sent["human_lock"] is True and not_sent["ai_enabled"] is True


def test_polling_tabs_share_one_servicenow_call(api):
    client, fake = api

    for _ in range(5):
        assert client.get("/api/v1/dashboard/incidents?limit=20").status_code == 200

    assert fake.calls == [(20, 0)]


def test_creating_from_the_dashboard_refreshes_the_list(api, monkeypatch):
    client, fake = api
    client.get("/api/v1/dashboard/incidents?limit=20")

    class Creator:
        def get_choices(self, table, element):
            return [{"label": "Network", "value": "network"}]

        def create_incident(self, *args, **kwargs):
            return {"sys_id": "sys-3", "number": "INC0010003"}

    monkeypatch.setattr("src.servicenow.client.ServiceNowClient", Creator)
    fake.rows.insert(0, _sn_row("INC0010003", "sys-3"))
    assert client.post(
        "/api/v1/dashboard/incidents", json={"short_description": "VPN", "category": "network"}
    ).status_code == 201

    body = client.get("/api/v1/dashboard/incidents?limit=20").json()
    assert body["incidents"][0]["number"] == "INC0010003"
    assert len(fake.calls) == 2


def test_servicenow_outage_serves_the_last_list_marked_stale(api, monkeypatch):
    client, fake = api
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    client.get("/api/v1/dashboard/incidents?limit=20")
    fake.error = ConnectionError("instance asleep")

    body = client.get("/api/v1/dashboard/incidents?limit=20").json()

    assert body["stale"] is True and "instance asleep" in body["stale_reason"]
    assert [i["number"] for i in body["incidents"]] == ["INC0010002", "INC0010001"]


def test_servicenow_outage_with_nothing_cached_is_502(api):
    client, fake = api
    fake.error = ConnectionError("instance asleep")

    assert client.get("/api/v1/dashboard/incidents").status_code == 502


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "offset=-1"])
def test_page_bounds_are_validated(api, query):
    client, fake = api
    assert client.get(f"/api/v1/dashboard/incidents?{query}").status_code == 422
    assert fake.calls == []


# _latest_runs against the real database

@pytest.fixture
def two_runs():
    number = f"INC-DASH-{uuid4().hex[:8]}"
    old, new = f"dash-old-{uuid4().hex}", f"dash-new-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add_all([
            Execution(execution_identifier=old, incident_reference=number, status="failed",
                      started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9)),
            Execution(execution_identifier=new, incident_reference=number, status="succeeded",
                      started_at=now - timedelta(minutes=2), ended_at=now),
        ])
        db.flush()
        db.add_all([
            WorkflowState(execution_reference=new, node_name="classify",
                          checkpoint=json.dumps({"classification": "network"}),
                          created_at=now - timedelta(minutes=1)),
            WorkflowState(execution_reference=new, node_name="act",
                          checkpoint=json.dumps({"action_taken": "resolved_automatically"}),
                          created_at=now),
            Failure(execution_reference=new, failing_node="act", error_class="Timeout",
                    message="slow", retry_count=1),
            RetryState(execution_reference=new, attempt_count=1),
        ])
        db.commit()
        yield number, new
    finally:
        for model in (WorkflowState, Failure, RetryState):
            db.query(model).filter(model.execution_reference.in_([old, new])).delete(synchronize_session=False)
        db.query(Execution).filter(Execution.incident_reference == number).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_latest_run_per_incident_with_its_latest_checkpoint(two_runs):
    number, newest = two_runs
    db = SessionLocal()
    try:
        runs = dashboard._latest_runs(db, [number, "INC-NEVER-PROCESSED"])
    finally:
        db.close()

    assert list(runs) == [number]
    run = runs[number]
    assert run["execution_id"] == newest
    assert run["status"] == "succeeded"
    assert run["latest_result"] == {"action_taken": "resolved_automatically"}
    assert run["failures"][0]["failing_node"] == "act"
    assert run["retry_attempt_count"] == 1
    assert run["duration_seconds"] == pytest.approx(120, abs=1)

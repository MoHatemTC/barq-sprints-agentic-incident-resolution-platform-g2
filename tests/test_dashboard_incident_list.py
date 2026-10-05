"""Dashboard incident list: ServiceNow is the source of truth.

Every incident in ServiceNow is listed (not only the ones the AI processed),
each with its latest AI run from Postgres. ServiceNow is read through a short
shared cache so dashboard polling never competes with the AI worker.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers import dashboard
from src.db.database import SessionLocal
from src.db.models import Execution, Failure, RetryState, WorkflowState
from tests.conftest import operator_headers


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
        self.rows, self.calls, self.queries, self.error = rows, [], [], None

    def list_incidents(self, fields, limit=20, offset=0, query=None):
        self.calls.append((limit, offset))
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.rows[offset:offset + limit], len(self.rows)


RUN = {"execution_id": "run-1", "status": "succeeded"}


@pytest.fixture
def api(api_client, monkeypatch):
    fake = FakeServiceNow([
        _sn_row("INC0010002", "sys-2"),
        _sn_row("INC0010001", "sys-1", category="inquiry", human_lock="true"),
    ])
    monkeypatch.setattr(dashboard, "_servicenow", lambda: fake)
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=60))
    monkeypatch.setattr(dashboard, "_latest_runs", lambda db, numbers, live_graph=None: {"INC0010002": RUN})
    monkeypatch.setattr(dashboard, "_live_graph", lambda: None)
    monkeypatch.setattr(dashboard, "SessionLocal", lambda: type("DB", (), {"close": lambda self: None})())
    # Use the main app client with auth headers
    api_client.headers.update(operator_headers())
    return api_client, fake


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
    from src.servicenow import config
    assert processed["servicenow_url"] == f"{config.INSTANCE_URL}/nav_to.do?uri=incident.do%3Fsys_id%3Dsys-2"
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


def test_servicenow_outage_serves_the_last_list_marked_delayed(api, monkeypatch):
    client, fake = api
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    first = client.get("/api/v1/dashboard/incidents?limit=20").json()
    fake.error = ConnectionError("instance asleep")

    body = client.get("/api/v1/dashboard/incidents?limit=20").json()

    assert body["delayed"] is True and "instance asleep" in body["sync_error"]
    assert body["stale"] is False                       # still within the 30s window
    assert body["synced_at"] == first["synced_at"]      # the time of the copy, not of this request
    assert [i["number"] for i in body["incidents"]] == ["INC0010002", "INC0010001"]


def _clock(monkeypatch, moment):
    monkeypatch.setattr(dashboard, "_utcnow", lambda: moment)


def test_fresh_list_reports_when_it_was_synced(api, monkeypatch):
    client, _ = api
    _clock(monkeypatch, datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))

    body = client.get("/api/v1/dashboard/incidents?limit=20").json()

    assert body["synced_at"] == "2026-10-01T12:00:00+00:00"
    assert body["age_seconds"] == 0 and body["stale_after_seconds"] == 30
    assert body["stale"] is False and body["delayed"] is False and body["sync_error"] is None


def test_outage_longer_than_the_threshold_is_stale(api, monkeypatch):
    client, fake = api
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    start = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    _clock(monkeypatch, start)
    client.get("/api/v1/dashboard/incidents?limit=20")
    fake.error = ConnectionError("instance asleep")

    _clock(monkeypatch, start + timedelta(seconds=30))
    assert client.get("/api/v1/dashboard/incidents?limit=20").json()["stale"] is False

    _clock(monkeypatch, start + timedelta(seconds=31))
    body = client.get("/api/v1/dashboard/incidents?limit=20").json()
    assert body["stale"] is True and body["age_seconds"] == 31
    assert body["synced_at"] == "2026-10-01T12:00:00+00:00"
    assert len(body["incidents"]) == 2                  # nothing dropped, nothing duplicated


def test_recovery_after_an_outage_is_fresh_again(api, monkeypatch):
    client, fake = api
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    start = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    _clock(monkeypatch, start)
    client.get("/api/v1/dashboard/incidents?limit=20")
    fake.error = ConnectionError("instance asleep")
    _clock(monkeypatch, start + timedelta(seconds=60))
    assert client.get("/api/v1/dashboard/incidents?limit=20").json()["stale"] is True

    fake.error = None
    body = client.get("/api/v1/dashboard/incidents?limit=20").json()

    assert body["stale"] is False and body["delayed"] is False and body["age_seconds"] == 0


def test_servicenow_outage_with_nothing_cached_is_502(api):
    client, fake = api
    fake.error = ConnectionError("instance asleep")

    assert client.get("/api/v1/dashboard/incidents").status_code == 502


def test_servicenow_errors_are_shown_as_one_short_line():
    from src.servicenow.exceptions import (
        ServiceNowAuthError, ServiceNowNetworkError, ServiceNowServerError,
    )
    refused = ServiceNowNetworkError(0, "HTTPSConnectionPool(host='dev.service-now.com', port=443): "
                                        "Max retries exceeded with url: /api/now/table/incident?sysparm_fields=x_secret "
                                        "(Caused by NewConnectionError(... [Errno 111] Connection refused))")

    assert dashboard._sync_error_text(refused) == "ServiceNow could not be reached (connection refused)"
    assert dashboard._sync_error_text(ServiceNowNetworkError(0, "Read timed out. (read timeout=30)")) == \
        "ServiceNow could not be reached (timed out)"
    assert dashboard._sync_error_text(ServiceNowAuthError(401, "{...}")) == \
        "ServiceNow rejected the integration login (401)"
    assert dashboard._sync_error_text(ServiceNowServerError(503, "<html>Hibernating</html>")) == \
        "ServiceNow returned an error (503)"


def test_outage_reason_has_no_urls_or_field_names(api, monkeypatch):
    from src.servicenow.exceptions import ServiceNowNetworkError
    client, fake = api
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    client.get("/api/v1/dashboard/incidents?limit=20")
    fake.error = ServiceNowNetworkError(0, "url: /api/now/table/incident?sysparm_fields=x_2215689 Connection refused")

    stale = client.get("/api/v1/dashboard/incidents?limit=20").json()
    first_load = client.get("/api/v1/dashboard/incidents?limit=5")

    assert stale["sync_error"] == "ServiceNow could not be reached (connection refused)"
    assert first_load.status_code == 502
    assert "sysparm" not in first_load.text and "x_2215689" not in first_load.text


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "offset=-1"])
def test_page_bounds_are_validated(api, query):
    client, fake = api
    assert client.get(f"/api/v1/dashboard/incidents?{query}").status_code == 422
    assert fake.calls == []


# search

def test_search_matches_number_or_description_words():
    one = "numberLIKEvpn^ORshort_descriptionLIKEvpn^ORdescriptionLIKEvpn"
    assert dashboard._search_query("vpn") == one
    assert dashboard._search_query("  INC0010275 ") == (
        "numberLIKEINC0010275^ORshort_descriptionLIKEINC0010275^ORdescriptionLIKEINC0010275"
    )
    # every word must match somewhere: (vpn in any field) AND (reset in any field)
    assert dashboard._search_query("vpn reset") == one + "^" + one.replace("vpn", "reset")
    assert dashboard._search_query("") is None and dashboard._search_query("   ") is None


def test_search_text_cannot_add_filters():
    """'^' starts a new clause in an encoded query; typed text must stay a search term."""
    query = dashboard._search_query("vpn^active=false^ORDERBYsys_id")
    clauses = query.split("^")
    assert all(c.startswith(("numberLIKE", "ORshort_descriptionLIKE", "ORdescriptionLIKE")) for c in clauses)
    assert "vpn" in query and "active=false" in query  # kept only as words to look for
    assert len(dashboard._search_query("a b c d e f g").split("^")) == 3 * dashboard.MAX_SEARCH_WORDS


def test_search_goes_to_servicenow_and_is_cached_per_query(api):
    client, fake = api

    body = client.get("/api/v1/dashboard/incidents?limit=20&q=vpn").json()
    client.get("/api/v1/dashboard/incidents?limit=20&q=vpn")
    client.get("/api/v1/dashboard/incidents?limit=20")

    assert body["q"] == "vpn"
    assert fake.queries == [dashboard._search_query("vpn"), None]  # one call per distinct search


def test_search_text_length_is_bounded(api):
    client, fake = api
    assert client.get("/api/v1/dashboard/incidents?q=" + "x" * 101).status_code == 422
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


# live progress of an in-progress run

class FakeGraph:
    """LangGraph's per-node checkpoint as the worker leaves it mid-run."""

    def __init__(self, next_node="retrieve", error=None):
        self.next_node, self.error, self.calls = next_node, error, []

    def get_state(self, config):
        self.calls.append(config["configurable"]["thread_id"])
        if self.error:
            raise self.error
        return SimpleNamespace(
            values={"classification": "network", "risk": "low", "incident_payload": {"sys_id": "x"}},
            next=(self.next_node,),
        )


@pytest.fixture
def running_run():
    number, eid = f"INC-LIVE-{uuid4().hex[:8]}", f"dash-live-{uuid4().hex}"
    db = SessionLocal()
    try:
        db.add(Execution(execution_identifier=eid, incident_reference=number, status="started"))
        db.commit()
        yield number, eid
    finally:
        db.query(Execution).filter(Execution.execution_identifier == eid).delete(synchronize_session=False)
        db.commit()
        db.close()


def _runs(numbers, graph):
    looked_up = []

    def live_graph():
        looked_up.append(True)
        return graph

    db = SessionLocal()
    try:
        return dashboard._latest_runs(db, numbers, live_graph=live_graph), looked_up
    finally:
        db.close()


def test_running_incident_shows_the_node_running_now(running_run):
    number, eid = running_run
    graph = FakeGraph(next_node="retrieve")

    runs, _ = _runs([number], graph)
    run = runs[number]

    assert graph.calls == [eid]
    assert run["live_node"] == "retrieve"
    assert run["latest_result"] == {"classification": "network", "risk": "low"}  # payload left out


def test_finished_runs_do_not_touch_the_checkpoint_store(two_runs):
    number, _ = two_runs

    runs, looked_up = _runs([number], FakeGraph())
    run = runs[number]

    assert looked_up == []  # no run in progress: the graph is never even built
    assert run["live_node"] is None
    assert run["latest_result"] == {"action_taken": "resolved_automatically"}


def test_unreadable_live_state_keeps_the_list_working(running_run):
    number, _ = running_run

    runs, _ = _runs([number], FakeGraph(error=RuntimeError("checkpoint store down")))
    assert runs[number]["status"] == "started" and runs[number]["live_node"] is None

    runs, _ = _runs([number], None)  # graph could not be built at all
    assert runs[number]["status"] == "started" and runs[number]["live_node"] is None


def test_human_rejected_incident_listed_with_state():
    fields = dashboard._incident_fields()
    row = {
        fields["sys_id"]: _cell("sys-rej"),
        fields["number"]: _cell("INC0019999"),
        fields["short_description"]: _cell("Issue rejected"),
        fields["category"]: _cell("network", "Network"),
        fields["state"]: _cell("1", "New"),
        fields["priority"]: _cell("4", "4 - Low"),
        fields["created_at"]: _cell("2026-09-29 08:30:00", "29/09/2026 11:30:00"),
        fields["ai_processing_state"]: _cell("human_rejected", "Human rejected"),
        fields["human_lock"]: _cell("false"),
        fields["ai_enabled"]: _cell("true"),
    }
    incident = dashboard._incident_from_servicenow(row, fields)
    assert incident["ai_processing_state"] == "Human rejected"


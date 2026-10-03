"""ServiceNow contract tests against recorded real responses (no network).

tests/fixtures/servicenow/ holds responses captured from the dev instance on
2026-10-03. The client and the dashboard must parse them, and must fail with a
clear ServiceNowContractError, not a KeyError, when the shape changes.
The same checks run against the live instance in test_servicenow_contract_live.py.
"""

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers import dashboard
from src.servicenow import contracts
from src.servicenow.client import ServiceNowClient
from src.servicenow.exceptions import ServiceNowContractError

FIXTURES = Path(__file__).parent / "fixtures" / "servicenow"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _http(body, headers=None):
    response = Mock()
    response.status_code = 200
    response.json.return_value = body
    response.headers = headers or {}
    return response


@pytest.fixture
def client():
    with patch("src.servicenow.auth.TokenManager.headers", return_value={}):
        yield ServiceNowClient()


@pytest.fixture
def page():
    return _fixture("incident_page.json")


DASHBOARD_FIELDS = list(dashboard._incident_fields().values())


# The recorded responses satisfy the contract and parse as the dashboard expects

def test_recorded_incident_page_parses(client, page):
    with patch("src.servicenow.client.requests.request", return_value=_http(page["body"], page["headers"])):
        rows, total = client.list_incidents(DASHBOARD_FIELDS, limit=2)

    assert total == 2
    contracts.display_value_rows(rows, DASHBOARD_FIELDS, "recorded page")
    incident = dashboard._incident_from_servicenow(rows[0], dashboard._incident_fields())
    assert incident["number"] == "INC0010396"
    assert incident["category"] == "Hardware"                     # display label
    assert incident["ai_processing_state"] == "Complete"
    assert incident["created_at"] == "2026-10-03T16:37:17+00:00"   # UTC value, not the display string
    assert incident["human_lock"] is False and incident["ai_enabled"] is True


def test_recorded_category_choices_parse(client):
    with patch("src.servicenow.client.requests.request",
               return_value=_http(_fixture("ui_meta_incident_category.json"))):
        choices = client.get_choices("incident", "category")

    values = [c["value"] for c in choices]
    assert values[0] == "inquiry" and "" not in values            # "-- None --" is dropped
    from src.workers.delivery_sweep import SUPPORTED_CATEGORIES
    assert set(SUPPORTED_CATEGORIES) <= set(values)


def test_recorded_correlation_lookup_parses(client):
    with patch("src.servicenow.client.requests.request",
               return_value=_http(_fixture("correlation_lookup.json"))):
        found = client.find_incident_by_correlation("barq-dashboard-s44-evidence-0001")

    assert found == {"sys_id": "3616ff8cc3330750b9523342b4013143", "number": "INC0010398"}


# A changed shape fails loudly with what changed

def _without(page, field):
    body = copy.deepcopy(page["body"])
    del body["result"][0][field]
    return body


@pytest.mark.parametrize("mutate, message", [
    (lambda p: _without(p, "category"), "row 0 is missing category"),
    (lambda p: {"result": [{**p["body"]["result"][0], "number": "INC0010396"}]},
     "row 0 field number is not {value, display_value}"),
])
def test_changed_incident_rows_are_reported(page, mutate, message):
    with pytest.raises(ServiceNowContractError, match=message):
        contracts.display_value_rows(mutate(page)["result"], DASHBOARD_FIELDS, "incident list")


@pytest.mark.parametrize("body, headers, message", [
    ({"result": {"sys_id": "x"}}, {}, "'result' is dict, expected a list"),
    ({"records": []}, {}, "'result' is NoneType, expected a list"),
    ({"result": ["INC1"]}, {}, "row 0 is str, expected an object"),
    ({"result": []}, {"X-Total-Count": "many"}, "X-Total-Count is 'many'"),
])
def test_changed_list_responses_are_reported(client, body, headers, message):
    with patch("src.servicenow.client.requests.request", return_value=_http(body, headers)):
        with pytest.raises(ServiceNowContractError, match=message):
            client.list_incidents(["number"])


def test_created_incident_without_number_is_reported(client):
    with patch("src.servicenow.client.requests.request", return_value=_http({"result": {"sys_id": "abc"}})):
        with pytest.raises(ServiceNowContractError, match="created incident: missing number"):
            client.create_incident("VPN down", category="network")


def test_missing_choice_column_is_reported(client):
    meta = {"result": {"columns": {"priority": {"choices": []}}}}
    with patch("src.servicenow.client.requests.request", return_value=_http(meta)):
        with pytest.raises(ServiceNowContractError, match="no column 'category'"):
            client.get_choices("incident", "category")


# The dashboard tells a format change apart from an outage

class _ChangedServiceNow:
    def __init__(self, rows):
        self.rows = rows

    def list_incidents(self, fields, limit=20, offset=0, query=None):
        return self.rows, len(self.rows)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(dashboard, "_incident_pages", dashboard._PageCache(ttl=0))
    monkeypatch.setattr(dashboard, "_latest_runs", lambda db, numbers, live_graph=None: {})
    monkeypatch.setattr(dashboard, "SessionLocal", lambda: type("DB", (), {"close": lambda self: None})())
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def test_dashboard_reports_a_format_change_without_field_names(api, page, monkeypatch):
    monkeypatch.setattr(dashboard, "_servicenow", lambda: _ChangedServiceNow(page["body"]["result"]))
    assert api.get("/api/v1/dashboard/incidents?limit=2").json()["delayed"] is False

    changed = _without(page, "x_2215689_ai_inc_0_ai_enabled")["result"]
    monkeypatch.setattr(dashboard, "_servicenow", lambda: _ChangedServiceNow(changed))
    body = api.get("/api/v1/dashboard/incidents?limit=2").json()
    fresh = api.get("/api/v1/dashboard/incidents?limit=3")

    # the last good page stays, marked delayed, with a reason that is not "could not be reached"
    assert body["delayed"] is True and len(body["incidents"]) == 2
    assert body["sync_error"] == "ServiceNow response format changed (details in the API log)"
    assert fresh.status_code == 502 and "x_2215689" not in fresh.text

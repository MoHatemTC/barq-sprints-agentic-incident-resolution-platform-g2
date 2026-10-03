"""Dashboard "New incident" must behave like the ServiceNow form.

It only creates the incident in ServiceNow. Whether the incident is processed
is decided by the AI Eligibility Check Business Rule, which calls the webhook;
the dashboard must never persist an event or push to Redis itself.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers import dashboard

CHOICES = [
    {"label": "Inquiry / Help", "value": "inquiry"},
    {"label": "Software", "value": "software"},
    {"label": "Software", "value": "software"},  # duplicate row, e.g. another domain
    {"label": "Network", "value": "network"},
]


class FakeServiceNowClient:
    created = []
    by_correlation = {}      # correlation_id -> record, like the incident table
    lose_create_answer = None  # error raised after the record is created (lost response)
    lookup_error = None

    def get_choices(self, table, element):
        assert (table, element) == ("incident", "category")
        return CHOICES

    def create_incident(self, short_description, description=None, caller_id=None, category=None,
                        correlation_id=None):
        self.created.append(
            {"short_description": short_description, "description": description, "category": category}
        )
        record = {"sys_id": f"sys-{len(self.created)}", "number": f"INC00101{len(self.created):02d}"}
        if len(self.created) == 1:
            record = {"sys_id": "abc123", "number": "INC0010100"}
        if correlation_id:
            self.by_correlation[correlation_id] = record
        if self.lose_create_answer:
            error, FakeServiceNowClient.lose_create_answer = self.lose_create_answer, None
            raise error
        return record

    def find_incident_by_correlation(self, correlation_id):
        if self.lookup_error:
            raise self.lookup_error
        return self.by_correlation.get(correlation_id)


@pytest.fixture
def api(monkeypatch):
    FakeServiceNowClient.created = []
    FakeServiceNowClient.by_correlation = {}
    FakeServiceNowClient.lose_create_answer = None
    FakeServiceNowClient.lookup_error = None
    monkeypatch.setattr("src.servicenow.client.ServiceNowClient", FakeServiceNowClient)

    def _no_db():
        raise AssertionError("dashboard create must not touch the database")

    monkeypatch.setattr(dashboard, "SessionLocal", _no_db)
    app = FastAPI()
    app.include_router(dashboard.router)
    return TestClient(app)


def test_categories_are_servicenow_choices_in_form_order(api):
    response = api.get("/api/v1/dashboard/incident-categories")

    assert response.status_code == 200
    assert response.json()["categories"] == [
        {"value": "inquiry", "label": "Inquiry / Help"},
        {"value": "software", "label": "Software"},
        {"value": "network", "label": "Network"},
    ]


def test_create_only_creates_the_incident_in_servicenow(api):
    response = api.post(
        "/api/v1/dashboard/incidents",
        json={"short_description": " VPN fails ", "description": "since 9am", "category": "network"},
    )

    assert response.status_code == 201
    assert response.json() == {"status": "created", "sys_id": "abc123", "number": "INC0010100"}
    assert FakeServiceNowClient.created == [
        {"short_description": "VPN fails", "description": "since 9am", "category": "network"}
    ]


def test_ineligible_category_is_still_created_so_the_business_rule_decides(api):
    response = api.post(
        "/api/v1/dashboard/incidents",
        json={"short_description": "How do I request a laptop?", "category": "inquiry"},
    )

    assert response.status_code == 201
    assert FakeServiceNowClient.created[0]["category"] == "inquiry"


def test_category_not_in_servicenow_is_rejected(api):
    response = api.post(
        "/api/v1/dashboard/incidents",
        json={"short_description": "VPN fails", "category": "made_up"},
    )

    assert response.status_code == 422
    assert FakeServiceNowClient.created == []


@pytest.mark.parametrize("body", [
    {"short_description": "VPN fails"},
    {"short_description": "   ", "category": "network"},
    {"category": "network"},
])
def test_short_description_and_category_are_required(api, body):
    assert api.post("/api/v1/dashboard/incidents", json=body).status_code == 422
    assert FakeServiceNowClient.created == []


def test_existing_sys_id_shortcut_is_gone(api):
    # The old dialog could push any existing incident straight onto the queue,
    # skipping every Business Rule check. Extra fields are now simply ignored.
    response = api.post(
        "/api/v1/dashboard/incidents",
        json={"short_description": "x", "category": "software", "sys_id": "locked123"},
    )

    assert response.status_code == 201
    assert response.json()["sys_id"] == "abc123"


# No duplicates when the same form is submitted again

FORM = {"short_description": "VPN fails", "category": "network", "request_id": "7f3c2a10-aaaa-bbbb"}


def test_retry_of_the_same_form_returns_the_first_incident(api):
    first = api.post("/api/v1/dashboard/incidents", json=FORM)
    retry = api.post("/api/v1/dashboard/incidents", json=FORM)

    assert first.status_code == 201 and first.json()["status"] == "created"
    assert retry.status_code == 200
    assert retry.json() == {"status": "already_created", "sys_id": "abc123", "number": "INC0010100"}
    assert len(FakeServiceNowClient.created) == 1
    assert list(FakeServiceNowClient.by_correlation) == ["barq-dashboard-7f3c2a10-aaaa-bbbb"]


def test_lost_answer_then_retry_makes_one_incident(api):
    from src.servicenow.exceptions import ServiceNowNetworkError
    FakeServiceNowClient.lose_create_answer = ServiceNowNetworkError(0, "Read timed out. (read timeout=30)")

    lost = api.post("/api/v1/dashboard/incidents", json=FORM)
    retry = api.post("/api/v1/dashboard/incidents", json=FORM)

    assert lost.status_code == 502
    assert "ServiceNow could not be reached (timed out)" in lost.text
    assert retry.status_code == 200 and retry.json()["number"] == "INC0010100"
    assert len(FakeServiceNowClient.created) == 1


def test_a_new_form_is_a_new_incident(api):
    api.post("/api/v1/dashboard/incidents", json=FORM)
    other = api.post("/api/v1/dashboard/incidents", json={**FORM, "request_id": "0d9e8f7a-cccc-dddd"})

    assert other.status_code == 201 and other.json()["number"] != "INC0010100"
    assert len(FakeServiceNowClient.created) == 2


def test_lookup_failure_creates_nothing(api):
    from src.servicenow.exceptions import ServiceNowServerError
    FakeServiceNowClient.lookup_error = ServiceNowServerError(503, "<html>Hibernating</html>")

    response = api.post("/api/v1/dashboard/incidents", json=FORM)

    assert response.status_code == 502 and "returned an error (503)" in response.text
    assert FakeServiceNowClient.created == []


@pytest.mark.parametrize("request_id", ["short", "x^ORsys_id!=0-padding", "a" * 65])
def test_request_id_cannot_inject_into_the_servicenow_query(api, request_id):
    response = api.post("/api/v1/dashboard/incidents", json={**FORM, "request_id": request_id})

    assert response.status_code == 422
    assert FakeServiceNowClient.created == []

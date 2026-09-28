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

    def get_choices(self, table, element):
        assert (table, element) == ("incident", "category")
        return CHOICES

    def create_incident(self, short_description, description=None, caller_id=None, category=None):
        self.created.append(
            {"short_description": short_description, "description": description, "category": category}
        )
        return {"sys_id": "abc123", "number": "INC0010100"}


@pytest.fixture
def api(monkeypatch):
    FakeServiceNowClient.created = []
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

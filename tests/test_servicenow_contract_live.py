"""ServiceNow contract tests against the LIVE instance (read-only).

The same checks as test_servicenow_contracts.py, on real responses, so a change
on the ServiceNow side (renamed field, ACL, removed category, new response
format) is caught by the nightly Live ServiceNow workflow
(.github/workflows/live-servicenow.yml), not by users seeing retry banners.
Skipped unless INCIDENT_SYS_ID is set, like test_integration.py.
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

SYS_ID = os.getenv("INCIDENT_SYS_ID")
pytestmark = pytest.mark.skipif(not SYS_ID, reason="INCIDENT_SYS_ID not set")


@pytest.fixture(scope="module")
def client():
    from src.servicenow.client import ServiceNowClient

    return ServiceNowClient()


def test_dashboard_incident_page_matches_the_contract(client):
    from src.api.routers import dashboard
    from src.servicenow import contracts

    fields = dashboard._incident_fields()
    rows, total = client.list_incidents(list(fields.values()), limit=3)

    assert rows and total >= len(rows)
    contracts.display_value_rows(rows, list(fields.values()), "live incident page")
    incident = dashboard._incident_from_servicenow(rows[0], fields)
    assert incident["number"].startswith("INC") and len(incident["sys_id"]) == 32


def test_category_choices_still_include_every_supported_category(client):
    from src.workers.delivery_sweep import SUPPORTED_CATEGORIES

    values = {c["value"] for c in client.get_choices("incident", "category")}
    assert set(SUPPORTED_CATEGORIES) <= values


def test_correlation_lookup_matches_the_contract(client):
    assert client.find_incident_by_correlation("barq-dashboard-contract-check-none") is None


def test_ai_fields_are_still_readable_on_the_test_incident(client):
    from src.servicenow import config

    incident = client.get_incident(SYS_ID)
    assert incident["sys_id"] == SYS_ID and incident["number"]
    missing = [f for f in config.AI_FIELDS.values() if f not in incident]
    assert not missing, f"AI fields renamed or hidden by an ACL: {missing}"

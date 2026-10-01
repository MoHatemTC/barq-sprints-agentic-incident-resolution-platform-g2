# to test client.py in servicenow : run it "pytest tests/test_client.py -v"
from unittest.mock import patch, Mock

import pytest

from src.servicenow import exceptions as exc
from src.servicenow.client import ServiceNowClient


def _response(status, body=None):
    r = Mock()
    r.status_code = status
    r.text = "error body"
    r.json.return_value = {"result": body or {"sys_id": "abc"}}
    return r


@pytest.fixture
def client():
    # skip the real token call
    with patch("src.servicenow.auth.TokenManager.headers", return_value={}):
        yield ServiceNowClient()


def test_error_status_propagates_from_client(client):
    with patch("src.servicenow.client.requests.request", return_value=_response(403)):
        with pytest.raises(exc.ServiceNowPermissionError):
            client.get_incident("abc")


def test_401_refreshes_token_and_retries_once(client):
    with patch("src.servicenow.client.requests.request",
               side_effect=[_response(401), _response(200)]) as req:
        result = client.get_incident("abc")
    assert req.call_count == 2
    assert result["sys_id"] == "abc"


def test_log_write_returns_none_on_failure(client):
    with patch("src.servicenow.client.requests.request", return_value=_response(500)):
        assert client.write_execution_log("sys", "eid", "act", "failed") is None
        
def test_dropped_field_raises(client):
    # ServiceNow returns 200 but omits the field it silently dropped
    resp = _response(200, {"sys_id": "abc"})
    with patch("src.servicenow.client.requests.request", return_value=resp):
        with pytest.raises(exc.ServiceNowWriteNotAppliedError):
            client.update_incident("abc", {"classification": "network"})


def test_invalid_log_status_raises(client):
    with pytest.raises(ValueError):
        client.write_execution_log("sys", "eid", "act", "not_a_status")

def _page(rows, total):
    r = _response(200)
    r.json.return_value = {"result": rows}
    r.headers = {"X-Total-Count": str(total)}
    return r


def test_published_kb_articles_keeps_paging_past_acl_short_pages(client):
    # 5 rows in total, page_size 2; ServiceNow hid one row on the first page
    pages = [_page([{"sys_id": "a"}], 5), _page([{"sys_id": "b"}, {"sys_id": "c"}], 5),
             _page([{"sys_id": "d"}], 5)]
    with patch("src.servicenow.client.requests.request", side_effect=pages) as req:
        articles = client.get_published_kb_articles(page_size=2)

    assert [a["sys_id"] for a in articles] == ["a", "b", "c", "d"]
    assert [c.kwargs["params"]["sysparm_offset"] for c in req.call_args_list] == [0, 2, 4]


def test_published_kb_articles_are_limited_to_our_knowledge_base(client, monkeypatch):
    monkeypatch.setattr("src.servicenow.client.config.KB_SYS_ID", "kb123")
    with patch("src.servicenow.client.requests.request", return_value=_page([], 0)) as req:
        client.get_published_kb_articles()

    query = req.call_args.kwargs["params"]["sysparm_query"]
    assert query == "workflow_state=published^kb_knowledge_base=kb123^ORDERBYsys_id"


def test_create_incident_sends_category_and_description(client):
    with patch("src.servicenow.client.requests.request", return_value=_response(200)) as req:
        client.create_incident("VPN down", description="since 9am", category="network")

    assert req.call_args.kwargs["json"] == {
        "short_description": "VPN down", "description": "since 9am", "category": "network",
    }


def test_create_incident_sends_correlation_id_when_given(client):
    with patch("src.servicenow.client.requests.request", return_value=_response(200)) as req:
        client.create_incident("VPN down", category="network", correlation_id="barq-dashboard-abc12345")

    assert req.call_args.kwargs["json"]["correlation_id"] == "barq-dashboard-abc12345"


def test_find_incident_by_correlation(client):
    row = {"sys_id": "s1", "number": "INC0010100"}
    with patch("src.servicenow.client.requests.request", return_value=_response(200, [row])) as req:
        assert client.find_incident_by_correlation("barq-dashboard-abc12345") == row

    assert req.call_args.args[0] == "GET"
    assert req.call_args.kwargs["params"]["sysparm_query"] == "correlation_id=barq-dashboard-abc12345"

    none = _response(200)
    none.json.return_value = {"result": []}
    with patch("src.servicenow.client.requests.request", return_value=none):
        assert client.find_incident_by_correlation("barq-dashboard-none0000") is None


def test_get_choices_reads_the_form_choice_list_from_ui_meta(client):
    meta = {"columns": {"category": {"choices": [
        {"label": "-- None --", "value": ""},
        {"label": "Inquiry / Help", "value": "inquiry", "sequence": 1},
        {"label": "Software", "value": "software", "sequence": 2},
    ]}}}
    with patch("src.servicenow.client.requests.request", return_value=_response(200, meta)) as req:
        choices = client.get_choices("incident", "category")

    assert req.call_args.args[1].endswith("/api/now/ui/meta/incident")
    assert choices == [
        {"label": "Inquiry / Help", "value": "inquiry"},
        {"label": "Software", "value": "software"},
    ]

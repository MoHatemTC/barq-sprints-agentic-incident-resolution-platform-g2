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
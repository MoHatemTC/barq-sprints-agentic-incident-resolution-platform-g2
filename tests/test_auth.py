# to test auth.py of service now , run it "pytest tests/test_auth.py -v"
import time
from unittest.mock import patch, Mock
import pytest
from src.servicenow.auth import TokenManager
from src.servicenow.exceptions import ServiceNowAuthError


def _ok():
    r = Mock()
    r.status_code = 200
    r.json.return_value = {"access_token": "tok-1", "expires_in": 1799}
    return r


def test_fetches_once_then_caches():
    with patch("src.servicenow.auth.requests.post", return_value=_ok()) as post:
        tm = TokenManager()
        assert tm.get_token() == "tok-1"
        tm.get_token()
        assert post.call_count == 1


def test_refetches_when_expired_or_invalidated():
    with patch("src.servicenow.auth.requests.post", return_value=_ok()) as post:
        tm = TokenManager()
        tm.get_token()
        tm._expires_at = time.time() - 1        # simulate expiry
        tm.get_token()
        tm.invalidate()
        tm.get_token()
        assert post.call_count == 3


def test_failed_token_request_raises():
    bad = Mock()
    bad.status_code = 401
    with patch("src.servicenow.auth.requests.post", return_value=bad):
        with pytest.raises(ServiceNowAuthError):
            TokenManager().get_token()


def test_headers_carry_bearer_token():
    with patch("src.servicenow.auth.requests.post", return_value=_ok()):
        assert TokenManager().headers()["Authorization"] == "Bearer tok-1"
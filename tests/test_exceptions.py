# to test run it "pytest tests/test_exceptions.py -v"
import pytest
from src.servicenow import exceptions as exc

class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


@pytest.mark.parametrize("status,expected", [
    (401, exc.ServiceNowAuthError),
    (403, exc.ServiceNowPermissionError),
    (404, exc.ServiceNowNotFoundError),
    (409, exc.ServiceNowConflictError),
    (422, exc.ServiceNowValidationError),
    (500, exc.ServiceNowServerError),
    (503, exc.ServiceNowServerError),
    (418, exc.ServiceNowError),
])

def test_status_maps_to_exception(status, expected):
    assert exc.exception_for_status(status) is expected
    with pytest.raises(expected):
        exc.raise_for_status(FakeResponse(status, "err"))


def test_retryable_and_log_state():
    assert exc.ServiceNowAuthError(401, "x").retryable is True
    assert exc.ServiceNowServerError(503, "x").retryable is True
    assert exc.ServiceNowPermissionError(403, "x").log_state == "blocked"
    assert exc.ServiceNowNotFoundError(404, "x").log_state == "failed"


def test_success_and_truncation():
    exc.raise_for_status(FakeResponse(200))
    with pytest.raises(exc.ServiceNowError) as info:
        exc.raise_for_status(FakeResponse(500, "x" * 1000))
    assert len(info.value.message) == 300
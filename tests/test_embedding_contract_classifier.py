"""Unit tests for embedding_contract.py classifier and parser functions.

No network calls. Does NOT belong to _INTEGRATION_MODULES.
"""

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "embedding_contract.py"
    spec = importlib.util.spec_from_file_location("embedding_contract", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["embedding_contract"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()
classify = mod.classify
parse_retry_delay = mod._parse_retry_delay
normalize_model = mod._normalize_model


# ---------------------------------------------------------------------------
# classify tests
# ---------------------------------------------------------------------------

def test_429_per_day_quota_id():
    """Google-style 429 with quotaId containing PerDayPerProject... -> per_day"""
    body = '{"error": {"code": 429, "message": "Quota exceeded", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "PerDayPerProjectPerModel-FreeTierGenerateContent", "quotaType": "DAILY"}]}}'
    assert classify(429, body) == "per_day"


def test_429_per_minute_with_retry_delay():
    """429 with PerMinute and retryDelay -> per_minute and delay 34"""
    body = '{"error": {"code": 429, "message": "Rate limit exceeded PerMinute", "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]}'
    assert classify(429, body) == "per_minute"
    # Parse delay
    import httpx
    delay = mod._parse_retry_delay(body, httpx.Headers())
    assert delay == 34


def test_429_both_per_day_and_per_minute():
    """Body contains both per_day and per_minute -> per_day (checked FIRST)"""
    body = '{"error": {"code": 429, "message": "PerDay and PerMinute limits exceeded"}'
    assert classify(429, body) == "per_day"


def test_429_unrelated_body():
    """429 with unrelated body -> unclassified_429"""
    body = '{"error": "Rate limit exceeded, try again later"}'
    assert classify(429, body) == "unclassified_429"


def test_401_auth():
    """401 -> auth"""
    assert classify(401, '{"error": "Unauthorized"}') == "auth"


def test_403_auth():
    """403 -> auth"""
    assert classify(403, '{"error": "Forbidden"}') == "auth"


def test_404_not_found():
    """404 -> not_found"""
    assert classify(404, '{"error": "Not found"}') == "not_found"


def test_500_other():
    """500 -> other"""
    assert classify(500, '{"error": "Internal server error"}') == "other"


def test_429_non_json_text():
    """429 with plain text body containing per_minute"""
    assert classify(429, "Rate limit per minute exceeded") == "per_minute"
    assert classify(429, "Rate limit per_day exceeded") == "per_day"


# ---------------------------------------------------------------------------
# parse_retry_delay tests
# ---------------------------------------------------------------------------

def test_parse_retry_delay_from_body():
    """Parse retryDelay from JSON body"""
    import httpx
    body = '{"retryDelay": "34s"}'
    delay = mod._parse_retry_delay(body, httpx.Headers())
    assert delay == 34


def test_parse_retry_delay_from_retry_after_header():
    """Parse Retry-After header (seconds)"""
    import httpx
    body = "{}"
    headers = httpx.Headers({"Retry-After": "45"})
    delay = mod._parse_retry_delay(body, headers)
    assert delay == 45


def test_parse_retry_delay_body_overrides_header():
    """Body retryDelay takes precedence over Retry-After header"""
    import httpx
    body = '{"retryDelay": "10s"}'
    headers = httpx.Headers({"Retry-After": "60"})
    delay = mod._parse_retry_delay(body, headers)
    assert delay == 10


def test_parse_retry_delay_none():
    """No delay info -> None"""
    import httpx
    body = '{}'
    headers = httpx.Headers({})
    delay = mod._parse_retry_delay(body, headers)
    assert delay is None


# ---------------------------------------------------------------------------
# normalize_model tests
# ---------------------------------------------------------------------------

def test_normalize_model():
    """Normalize model name: lowercase, last path segment after '/'"""
    assert normalize_model("gemini/text-embedding-004") == "text-embedding-004"
    assert normalize_model("text-embedding-004") == "text-embedding-004"
    assert normalize_model("GEMINI/TEXT-EMBEDDING") == "text-embedding"
    assert normalize_model("  MyModel/v2  ") == "v2"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
"""API auth coverage tests.

- Every /api/v1/dashboard/* and /api/v1/approvals/* route requires auth.
- The by-incident route uses verify_token (webhook token), not operator JWT.
- /health stays open.
- CORS: allowed origins echoed; evil origin gets no ACAO; "*" raises at startup.
"""

import time
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from src.api.auth import verify_token


# Explicit allowlist of routes that are intentionally NOT protected by operator JWT
# (they use verify_token or are public). Add new routes here with a comment.
ALLOWLIST_NO_OPERATOR_JWT = {
    ("POST", "/api/v1/approvals/by-incident/{incident_sys_id}/decide"),  # verify_token (webhook)
}


def _operator_token(webhook_auth_token: str = "test-token-123", role: str = "operator") -> str:
    payload = {"role": role, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, webhook_auth_token, algorithm="HS256")


def operator_headers(webhook_auth_token: str = "test-token-123") -> dict:
    return {"Authorization": f"Bearer {_operator_token(webhook_auth_token)}"}


def non_operator_headers(webhook_auth_token: str = "test-token-123") -> dict:
    return {"Authorization": f"Bearer {_operator_token(webhook_auth_token, role='viewer')}"}


def _collect_protected_operations(app: FastAPI) -> list[tuple[str, str]]:
    """Return (method, path) for all operations under /api/v1/dashboard or /api/v1/approvals.

    Uses app.openapi()['paths'] which lists all included routes immediately after create_app().
    Each (HTTP method, path) pair is one operation (e.g., GET and POST on same path = 2 operations).
    """
    paths = app.openapi()["paths"]
    operations = []
    for path, methods in paths.items():
        if path.startswith("/api/v1/dashboard") or path.startswith("/api/v1/approvals"):
            for method in methods:
                if method != "head":  # HEAD mirrors GET
                    operations.append((method.upper(), path))
    return operations


def _fill_path_params(path: str) -> str:
    """Replace path parameters with dummy values for testing."""
    replacements = {
        "{incident_sys_id}": "sys-dummy",
        "{approval_id}": "appr-dummy",
        "{number}": "INC001",
        "{sys_id}": "sys-dummy",
        "{execution_id}": "exec-dummy",
        "{entry_id}": "1",
    }
    for param, value in replacements.items():
        path = path.replace(param, value)
    return path


def _webhook_headers(webhook_auth_token: str = "test-token-123") -> dict:
    return {"Authorization": f"Bearer {webhook_auth_token}"}


@pytest.fixture
def app_no_auth(test_settings):
    """App with NO auth overrides - real auth enforced."""
    from src.api.app import create_app
    from src.api.dependencies import get_settings, get_redis, get_db_session, get_sync_db
    from src.api.routers import approvals
    from langgraph.checkpoint.memory import MemorySaver
    from src.agent.graph import create_graph

    app = create_app(test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_sync_db] = lambda: MagicMock()
    # S3.4 approvals: no paused executions by default
    empty_store = MagicMock()
    empty_store.awaiting_execution_ids.return_value = []
    empty_store.executions_for_incident.return_value = []
    app.dependency_overrides[approvals.get_approval_store] = lambda: empty_store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: create_graph().compile(checkpointer=MemorySaver())
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: MagicMock()

    yield app
    app.dependency_overrides.clear()


def test_protected_routes_exist(app_no_auth):
    """Assert we actually found routes to test."""
    operations = _collect_protected_operations(app_no_auth)
    print(f"Found {len(operations)} protected operations:")
    for method, path in sorted(operations):
        print(f"  {method} {path}")
    assert operations, "No /api/v1/dashboard or /api/v1/approvals operations found"
    # Should have at least 13 operations (12 paths, with /incidents having GET+POST)
    assert len(operations) >= 13, f"Expected at least 13 operations, got {len(operations)}"


def test_all_protected_routes_return_401_without_credentials(app_no_auth):
    """Every dashboard/approvals operation returns 401 with no credentials."""
    operations = _collect_protected_operations(app_no_auth)
    with TestClient(app_no_auth) as client:
        for method, path in operations:
            test_path = _fill_path_params(path)
            resp = client.request(method, test_path)
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}, expected 401: {resp.text}"


def test_all_protected_routes_return_403_with_non_operator_role(app_no_auth):
    """Operator routes return 403 with valid JWT but wrong role."""
    operations = _collect_protected_operations(app_no_auth)
    with TestClient(app_no_auth) as client:
        for method, path in operations:
            if (method, path) in ALLOWLIST_NO_OPERATOR_JWT:
                continue  # this route uses verify_token, not operator JWT
            test_path = _fill_path_params(path)
            resp = client.request(method, test_path, headers=non_operator_headers())
            assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}, expected 403: {resp.text}"


def test_by_incident_route_accepts_webhook_token_not_operator_jwt(app_no_auth):
    """POST /api/v1/approvals/by-incident/{sys_id}/decide accepts webhook token, not operator JWT."""
    with TestClient(app_no_auth) as client:
        # With operator JWT -> 401 (verify_token expects webhook token)
        resp = client.post(
            "/api/v1/approvals/by-incident/sys-dummy/decide",
            json={"action": "approve", "reviewer": "test", "human_solution": "fix it"},
            headers=operator_headers(),
        )
        assert resp.status_code == 401, f"operator JWT should not work: {resp.status_code} {resp.text}"

        # With webhook token -> passes verify_token (then hits 404 because no execution, but auth passed)
        resp = client.post(
            "/api/v1/approvals/by-incident/sys-dummy/decide",
            json={"action": "approve", "reviewer": "test", "human_solution": "fix it"},
            headers=_webhook_headers(),
        )
        # 404 = auth passed, no execution found. 401 = auth failed.
        assert resp.status_code == 404, f"webhook token should pass verify_token: {resp.status_code} {resp.text}"


def test_health_stays_open(app_no_auth):
    """GET /health returns 200 without credentials."""
    with TestClient(app_no_auth) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.text == "OK"


def test_verify_token_constant_time(test_settings):
    """verify_token: correct token returns None; wrong token raises HTTPException 401; 'Bearer é' raises HTTPException 401."""
    # Correct token - should not raise
    result = verify_token(authorization=f"Bearer {test_settings.webhook_auth_token}", settings=test_settings)
    assert result is None

    # Wrong token
    with pytest.raises(HTTPException) as exc:
        verify_token(authorization="Bearer wrong-token", settings=test_settings)
    assert exc.value.status_code == 401

    # Non-ASCII header - should give 401 cleanly, not raise TypeError/500
    with pytest.raises(HTTPException) as exc:
        verify_token(authorization="Bearer é", settings=test_settings)
    assert exc.value.status_code == 401


def test_cors_preflight_allowed_origin(app_no_auth):
    """OPTIONS from allowed origin echoes that origin in ACAO."""
    with TestClient(app_no_auth) as client:
        resp = client.options(
            "/api/v1/dashboard/incidents",
            headers={
                "Origin": "http://localhost:8082",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8082"


def test_cors_preflight_evil_origin_no_acao(app_no_auth):
    """OPTIONS from evil origin gets NO access-control-allow-origin header."""
    with TestClient(app_no_auth) as client:
        resp = client.options(
            "/api/v1/dashboard/incidents",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should not have ACAO header at all (or empty)
        acao = resp.headers.get("access-control-allow-origin")
        assert not acao or acao == "", f"evil origin should not get ACAO, got: {acao}"


def test_cors_allowed_origins_star_raises():
    """CORS_ALLOWED_ORIGINS='*' raises ValueError at startup."""
    from src.api.app import create_app
    from src.api.schemas import Settings
    bad_settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        redis_host="localhost",
        redis_port=6379,
        webhook_auth_token="test-token-123",
        cors_allowed_origins="*",
    )
    with pytest.raises(ValueError, match="must not contain '\\*'"):
        create_app(bad_settings)
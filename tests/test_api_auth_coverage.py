"""API auth coverage tests.

- Enumerates ALL operations from app.openapi()["paths"]
- Every operation not in PUBLIC must return 401 without credentials
- Every operation not in PUBLIC and not in WEBHOOK_TOKEN must return 403 with viewer role
- WEBHOOK_TOKEN routes use verify_token (webhook token), not operator JWT
- /health, /ready, POST /api/v1/auth/token, GET /api/v1/config stay open
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from src.api.auth import verify_token
from tests.conftest import TEST_OPERATOR_JWT_SECRET, operator_headers, non_operator_headers


# Explicit PUBLIC allowlist: routes that MUST stay open (no auth required)
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("POST", "/api/v1/auth/token"),
    ("GET", "/api/v1/config"),
}

# Explicit WEBHOOK_TOKEN allowlist: routes that use verify_token (webhook token), not operator JWT
# These return 401 for operator JWT, 200/404 for correct webhook token
WEBHOOK_TOKEN = {
    ("POST", "/api/v1/webhook/incident"),
    ("POST", "/api/v1/approvals/by-incident/{incident_sys_id}/decide"),
    # Note: dlq replay uses require_operator_role (operator JWT), not verify_token
}


def _webhook_headers(webhook_auth_token: str = "test-token-123") -> dict:
    return {"Authorization": f"Bearer {webhook_auth_token}"}


def _collect_all_operations(app: FastAPI) -> list[tuple[str, str]]:
    """Return (method, path) for ALL operations from app.openapi()['paths']."""
    paths = app.openapi()["paths"]
    operations = []
    for path, methods in paths.items():
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
        "{event_id}": "evt-dummy",
    }
    for param, value in replacements.items():
        path = path.replace(param, value)
    return path


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


def test_operations_enumerated(app_no_auth):
    """Print and assert all operations with their auth group."""
    all_ops = _collect_all_operations(app_no_auth)

    # Classify each operation
    public_ops = []
    webhook_ops = []
    operator_ops = []

    for method, path in all_ops:
        if (method, path) in PUBLIC:
            group = "PUBLIC"
            public_ops.append((method, path))
        elif (method, path) in WEBHOOK_TOKEN:
            group = "WEBHOOK_TOKEN"
            webhook_ops.append((method, path))
        else:
            group = "OPERATOR"
            operator_ops.append((method, path))
        print(f"  {group}: {method} {path}")

    total = len(all_ops)
    print(f"\nTotal operations: {total}")
    print(f"  PUBLIC: {len(public_ops)}")
    print(f"  WEBHOOK_TOKEN: {len(webhook_ops)}")
    print(f"  OPERATOR: {len(operator_ops)}")

    # Assert we found at least the expected minimum (more than 13)
    assert total > 13, f"Expected more than 13 operations, got {total}"

    # Verify specific expected routes exist
    expected_public = {("GET", "/health"), ("GET", "/ready"), ("POST", "/api/v1/auth/token"), ("GET", "/api/v1/config")}
    for ep in expected_public:
        assert ep in all_ops, f"Missing expected PUBLIC operation: {ep}"

    expected_webhook = {("POST", "/api/v1/webhook/incident"), ("POST", "/api/v1/approvals/by-incident/{incident_sys_id}/decide"), ("POST", "/api/v1/dlq/{event_id}/replay")}
    for ep in expected_webhook:
        assert ep in all_ops, f"Missing expected WEBHOOK_TOKEN operation: {ep}"


def test_all_non_public_return_401_without_credentials(app_no_auth):
    """Every operation not in PUBLIC must return 401 with no credentials."""
    all_ops = _collect_all_operations(app_no_auth)
    with TestClient(app_no_auth) as client:
        for method, path in all_ops:
            if (method, path) in PUBLIC:
                continue
            test_path = _fill_path_params(path)
            resp = client.request(method, test_path)
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}, expected 401: {resp.text}"


def test_all_operator_routes_return_403_with_non_operator_role(app_no_auth):
    """Every OPERATOR operation returns 403 with valid JWT but wrong role (viewer)."""
    all_ops = _collect_all_operations(app_no_auth)
    with TestClient(app_no_auth) as client:
        for method, path in all_ops:
            if (method, path) in PUBLIC or (method, path) in WEBHOOK_TOKEN:
                continue
            test_path = _fill_path_params(path)
            resp = client.request(method, test_path, headers=non_operator_headers())
            assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}, expected 403: {resp.text}"


def test_webhook_token_routes_accept_webhook_not_operator(app_no_auth):
    """WEBHOOK_TOKEN routes accept webhook token, reject operator JWT."""
    with TestClient(app_no_auth) as client:
        for method, path in WEBHOOK_TOKEN:
            test_path = _fill_path_params(path)
            # Operator JWT -> 401 (verify_token expects webhook token)
            resp = client.request(method, test_path, json={}, headers=operator_headers())
            assert resp.status_code == 401, f"{method} {path}: operator JWT should not work: {resp.status_code} {resp.text}"

            # Webhook token -> passes verify_token (may hit 404/422 but auth passed)
            resp = client.request(method, test_path, json={}, headers=_webhook_headers())
            assert resp.status_code != 401, f"{method} {path}: webhook token should pass verify_token: {resp.status_code} {resp.text}"


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


def test_verify_token_empty_webhook_token_refuses_all():
    """verify_token with empty webhook_auth_token must reject all credentials including empty."""
    from src.api.auth import verify_token
    from src.api.schemas import Settings

    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        redis_host="localhost",
        redis_port=6379,
        webhook_auth_token="",  # EMPTY
        cors_allowed_origins="http://localhost:8082",
        operator_password="test-operator-password-123",
        operator_jwt_secret="test-operator-jwt-secret-min-32-chars-long",
    )

    test_cases = ["Bearer ", "Bearer", "", None]
    for auth_header in test_cases:
        with pytest.raises(HTTPException) as exc:
            verify_token(authorization=auth_header, settings=settings)
        assert exc.value.status_code == 401, f"auth_header={repr(auth_header)} should raise 401"


def test_verify_token_non_empty_webhook_token_works():
    """verify_token with non-empty webhook_auth_token accepts correct token."""
    from src.api.auth import verify_token
    from src.api.schemas import Settings

    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        redis_host="localhost",
        redis_port=6379,
        webhook_auth_token="test-token-123",
        cors_allowed_origins="http://localhost:8082",
        operator_password="test-operator-password-123",
        operator_jwt_secret="test-operator-jwt-secret-min-32-chars-long",
    )

    # Correct token - should not raise
    result = verify_token(authorization="Bearer test-token-123", settings=settings)
    assert result is None

    # Wrong token - should raise
    with pytest.raises(HTTPException) as exc:
        verify_token(authorization="Bearer wrong", settings=settings)
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
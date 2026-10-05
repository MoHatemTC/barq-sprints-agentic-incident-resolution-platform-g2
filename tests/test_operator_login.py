"""Operator login tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from src.api.app import create_app
from src.api.dependencies import get_settings, get_redis, get_db_session, get_sync_db
from src.api.routers import approvals
from src.api.auth import verify_token, decode_bearer_token, require_operator_role
from langgraph.checkpoint.memory import MemorySaver
from src.agent.graph import create_graph

from tests.conftest import (
    TEST_OPERATOR_PASSWORD,
    TEST_OPERATOR_JWT_SECRET,
    operator_headers,
    non_operator_headers,
)


def _test_settings(**overrides):
    from src.api.schemas import Settings
    base = dict(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        redis_host="localhost",
        redis_port=6379,
        webhook_auth_token="test-token-123",
        cors_allowed_origins="http://localhost:8082,http://127.0.0.1:8082,http://localhost:3000,http://127.0.0.1:3000",
        operator_password=TEST_OPERATOR_PASSWORD,
        operator_jwt_secret=TEST_OPERATOR_JWT_SECRET,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def app_with_auth(test_settings):
    """App with real auth enforced."""
    app = create_app(test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_sync_db] = lambda: MagicMock()
    empty_store = MagicMock()
    empty_store.awaiting_execution_ids.return_value = []
    empty_store.executions_for_incident.return_value = []
    app.dependency_overrides[approvals.get_approval_store] = lambda: empty_store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: create_graph().compile(checkpointer=MemorySaver())
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: MagicMock()

    yield app
    app.dependency_overrides.clear()


def test_wrong_password_returns_401(app_with_auth):
    with TestClient(app_with_auth) as client:
        resp = client.post(
            "/api/v1/auth/token",
            json={"password": "wrong"},
        )
        assert resp.status_code == 401


def test_empty_password_returns_401(app_with_auth):
    with TestClient(app_with_auth) as client:
        resp = client.post(
            "/api/v1/auth/token",
            json={"password": ""},
        )
        assert resp.status_code == 401


def test_right_password_returns_token(app_with_auth):
    with TestClient(app_with_auth) as client:
        resp = client.post(
            "/api/v1/auth/token",
            json={"password": TEST_OPERATOR_PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 8 * 60 * 60


def test_token_accepted_by_protected_route(app_with_auth):
    with TestClient(app_with_auth) as client:
        # Login
        resp = client.post(
            "/api/v1/auth/token",
            json={"password": TEST_OPERATOR_PASSWORD},
        )
        token = resp.json()["access_token"]

        # Use token on protected route
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/v1/approvals", headers=headers)
        assert resp.status_code == 200


def test_empty_operator_password_refuses_login(app_with_auth):
    """If OPERATOR_PASSWORD is empty, login always returns 401."""
    from src.api.app import create_app
    from src.api.schemas import Settings
    from src.api.dependencies import get_settings, get_redis, get_db_session, get_sync_db
    from unittest.mock import AsyncMock, MagicMock
    from src.api.routers import approvals
    from langgraph.checkpoint.memory import MemorySaver
    from src.agent.graph import create_graph

    settings = _test_settings(operator_password="")
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_sync_db] = lambda: MagicMock()
    empty_store = MagicMock()
    empty_store.awaiting_execution_ids.return_value = []
    empty_store.executions_for_incident.return_value = []
    app.dependency_overrides[approvals.get_approval_store] = lambda: empty_store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: create_graph().compile(checkpointer=MemorySaver())
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: MagicMock()

    with TestClient(app) as client:
        # Even empty password should be 401 (no info leak)
        resp = client.post("/api/v1/auth/token", json={"password": ""})
        assert resp.status_code == 401
        # Wrong password also 401
        resp = client.post("/api/v1/auth/token", json={"password": "anything"})
        assert resp.status_code == 401

    app.dependency_overrides.clear()


def test_empty_operator_jwt_secret_refuses_login_and_token(app_with_auth):
    """If OPERATOR_JWT_SECRET is empty, login returns 401 and tokens are never accepted."""
    from src.api.app import create_app
    from src.api.schemas import Settings
    from src.api.dependencies import get_settings, get_redis, get_db_session, get_sync_db
    from unittest.mock import AsyncMock, MagicMock
    from src.api.routers import approvals
    from langgraph.checkpoint.memory import MemorySaver
    from src.agent.graph import create_graph

    settings = _test_settings(operator_jwt_secret="")
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_sync_db] = lambda: MagicMock()
    empty_store = MagicMock()
    empty_store.awaiting_execution_ids.return_value = []
    empty_store.executions_for_incident.return_value = []
    app.dependency_overrides[approvals.get_approval_store] = lambda: empty_store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: create_graph().compile(checkpointer=MemorySaver())
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: MagicMock()

    with TestClient(app) as client:
        # Login fails
        resp = client.post("/api/v1/auth/token", json={"password": TEST_OPERATOR_PASSWORD})
        assert resp.status_code == 401

    app.dependency_overrides.clear()


def test_empty_webhook_auth_token_refuses_verify_token():
    """If WEBHOOK_AUTH_TOKEN is empty, verify_token must reject all credentials including empty."""
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
        operator_password=TEST_OPERATOR_PASSWORD,
        operator_jwt_secret=TEST_OPERATOR_JWT_SECRET,
    )

    test_cases = ["Bearer ", "Bearer", "", None]
    for auth_header in test_cases:
        with pytest.raises(HTTPException) as exc:
            verify_token(authorization=auth_header, settings=settings)
        assert exc.value.status_code == 401, f"auth_header={repr(auth_header)} should raise 401"


def test_non_empty_webhook_auth_token_verify_token_works():
    """verify_token with non-empty webhook_auth_token accepts correct token and rejects wrong."""
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
        operator_password=TEST_OPERATOR_PASSWORD,
        operator_jwt_secret=TEST_OPERATOR_JWT_SECRET,
    )

    # Correct token - should not raise
    result = verify_token(authorization="Bearer test-token-123", settings=settings)
    assert result is None

    # Wrong token - should raise
    with pytest.raises(HTTPException) as exc:
        verify_token(authorization="Bearer wrong", settings=settings)
    assert exc.value.status_code == 401


def test_expired_token_rejected(app_with_auth):
    import time
    import jwt
    expired_token = jwt.encode(
        {"role": "operator", "exp": int(time.time()) - 1},
        TEST_OPERATOR_JWT_SECRET,
        algorithm="HS256",
    )
    with TestClient(app_with_auth) as client:
        resp = client.get("/api/v1/approvals", headers={"Authorization": f"Bearer {expired_token}"})
        assert resp.status_code == 401


def test_token_without_exp_rejected(app_with_auth):
    import jwt
    no_exp_token = jwt.encode(
        {"role": "operator"},
        TEST_OPERATOR_JWT_SECRET,
        algorithm="HS256",
    )
    with TestClient(app_with_auth) as client:
        resp = client.get("/api/v1/approvals", headers={"Authorization": f"Bearer {no_exp_token}"})
        assert resp.status_code == 401


def test_token_signed_with_webhook_secret_rejected(app_with_auth):
    import jwt
    import time
    webhook_token = jwt.encode(
        {"role": "operator", "exp": int(time.time()) + 3600},
        "test-token-123",
        algorithm="HS256",
    )
    with TestClient(app_with_auth) as client:
        resp = client.get("/api/v1/approvals", headers={"Authorization": f"Bearer {webhook_token}"})
        assert resp.status_code == 401


def test_viewer_role_gets_403(app_with_auth):
    import jwt
    import time
    viewer_token = jwt.encode(
        {"role": "viewer", "exp": int(time.time()) + 3600},
        TEST_OPERATOR_JWT_SECRET,
        algorithm="HS256",
    )
    with TestClient(app_with_auth) as client:
        resp = client.get("/api/v1/approvals", headers={"Authorization": f"Bearer {viewer_token}"})
        assert resp.status_code == 403


def test_auth_token_endpoint_reachable_without_credentials(app_with_auth):
    with TestClient(app_with_auth) as client:
        # No Authorization header needed
        resp = client.post(
            "/api/v1/auth/token",
            json={"password": TEST_OPERATOR_PASSWORD},
        )
        assert resp.status_code == 200


def test_servicenow_route_still_uses_webhook_token(app_with_auth):
    with TestClient(app_with_auth) as client:
        # Webhook token works
        resp = client.post(
            "/api/v1/approvals/by-incident/sys-dummy/decide",
            json={"action": "approve", "reviewer": "test", "human_solution": "fix it"},
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 404  # auth passed, no execution

        # Operator JWT does NOT work on this route
        login = client.post("/api/v1/auth/token", json={"password": TEST_OPERATOR_PASSWORD})
        operator_token = login.json()["access_token"]
        resp = client.post(
            "/api/v1/approvals/by-incident/sys-dummy/decide",
            json={"action": "approve", "reviewer": "test", "human_solution": "fix it"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert resp.status_code == 401


def test_cors_null_raises():
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
        cors_allowed_origins="null",
    )
    with pytest.raises(ValueError, match="must not contain 'null'"):
        create_app(bad_settings)

    bad_settings2 = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        redis_host="localhost",
        redis_port=6379,
        webhook_auth_token="test-token-123",
        cors_allowed_origins="http://localhost:8082,NULL",
    )
    with pytest.raises(ValueError, match="must not contain 'null'"):
        create_app(bad_settings2)


def test_all_protected_operations_still_return_401_without_token(app_with_auth):
    """Enumerate app.openapi() paths; every dashboard/approvals operation returns 401 without token."""
    paths = app_with_auth.openapi()["paths"]
    operations = []
    for path, methods in paths.items():
        if path.startswith("/api/v1/dashboard") or path.startswith("/api/v1/approvals"):
            for method in methods:
                if method != "head":
                    operations.append((method.upper(), path))
    assert len(operations) >= 13, f"Expected at least 13 operations, got {len(operations)}"

    with TestClient(app_with_auth) as client:
        for method, path in operations:
            test_path = path.replace("{incident_sys_id}", "sys-dummy").replace("{approval_id}", "appr-dummy").replace("{number}", "INC001").replace("{sys_id}", "sys-dummy").replace("{execution_id}", "exec-dummy").replace("{entry_id}", "1")
            resp = client.request(method, test_path)
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
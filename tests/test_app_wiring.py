# tests/test_app_wiring.py — full corrected version

from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.middleware import CorrelationIDMiddleware


def test_create_app_returns_fastapi_instance():
    app = create_app()
    assert app is not None


def test_all_expected_routes_are_registered():
    app = create_app()

    expected_get = [
        "/health", "/ready", "/api/v1/config",
        "/api/v1/executions/exec-1",
        "/api/v1/executions/exec-1/trace",
        "/api/v1/incidents/sys-1/executions",
        "/api/v1/approvals",
        "/api/v1/dlq",
        "/api/v1/eval/results",
    ]

    with TestClient(app) as client:
        for path in expected_get:
            response = client.get(path)
            assert response.status_code != 404, f"Route not found: {path}"


def test_correlation_id_middleware_is_registered():
    app = create_app()
    middleware_classes = [m.cls for m in app.user_middleware]
    assert CorrelationIDMiddleware in middleware_classes


@patch("src.api.app.redis")
@patch("src.api.app.create_async_engine")
def test_lifespan_sets_app_state(mock_create_engine, mock_redis_module):
    mock_redis_module.from_url.return_value = AsyncMock()

    fake_engine = MagicMock()
    fake_engine.begin.return_value.__aenter__.return_value = AsyncMock()
    fake_engine.begin.return_value.__aexit__.return_value = None
    fake_engine.dispose = AsyncMock()
    mock_create_engine.return_value = fake_engine

    app = create_app()

    with TestClient(app) as client:
        assert hasattr(client.app.state, "redis")
        assert hasattr(client.app.state, "engine")
        assert hasattr(client.app.state, "async_session")


def test_response_has_correlation_id_header():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Correlation-ID": "wiring-test-1"})
        assert response.headers.get("x-correlation-id") == "wiring-test-1"
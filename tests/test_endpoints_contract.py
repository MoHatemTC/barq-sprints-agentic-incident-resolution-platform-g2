import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.dependencies import get_settings, get_redis, get_db_session
from src.api.auth import require_operator_role
from src.api.schemas import Settings
from src.api.routers import approvals
from langgraph.checkpoint.memory import MemorySaver
from src.agent.graph import create_graph

pytestmark = pytest.mark.usefixtures("hermetic_llm")

TEST_TOKEN = "test-token-123"

def get_test_settings() -> Settings:
    return Settings(
        postgres_host="localhost", postgres_port=5432,
        postgres_user="test", postgres_password="test", postgres_db="test",
        redis_host="localhost", redis_port=6379,
        webhook_auth_token=TEST_TOKEN,
    )

@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[get_settings] = get_test_settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()

    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[require_operator_role] = lambda: {"role": "operator"}
    # from S3.4 approvals: no paused executions, in-memory checkpoints
    empty_store = MagicMock()
    empty_store.awaiting_execution_ids.return_value = []
    app.dependency_overrides[approvals.get_approval_store] = lambda: empty_store
    app.dependency_overrides[approvals.get_approval_graph] = lambda: create_graph().compile(checkpointer=MemorySaver())
    app.dependency_overrides[approvals.get_resume_dispatcher] = lambda: MagicMock()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_ready_returns_200_when_dependencies_healthy(client):
    response = client.get("/ready")
    assert response.status_code == 200


def test_config_returns_expected_fields_and_excludes_secrets(client):
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {"postgres_host", "postgres_port", "redis_host", "redis_port"}
    for secret_field in ("postgres_password", "redis_password", "webhook_auth_token"):
        assert secret_field not in body


def test_get_execution_returns_expected_shape(client):
    response = client.get("/api/v1/executions/exec-123")
    assert response.status_code == 200
    body = response.json()
    for field in ("execution_id", "incident_sys_id", "status", "model", "agent_version"):
        assert field in body


def test_get_execution_trace_returns_expected_shape(client):
    response = client.get("/api/v1/executions/exec-123/trace")
    assert response.status_code == 200
    assert "nodes" in response.json()


def test_list_executions_for_incident_is_paginated(client):
    response = client.get("/api/v1/incidents/sys-123/executions?page=2&page_size=5")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2
    assert body["page_size"] == 5


def test_list_approvals_returns_expected_shape(client):
    response = client.get("/api/v1/approvals")
    assert response.status_code == 200
    assert "items" in response.json()


def test_decide_approval_rejects_invalid_action(client):
    response = client.post(
        "/api/v1/approvals/appr-123/decide",
        json={"action": "maybe", "reviewer": "sarah"},
    )
    assert response.status_code == 422


def test_decide_approval_unknown_execution_returns_404(client):
    # a valid action on an execution that was never paused
    response = client.post(
        "/api/v1/approvals/appr-123/decide",
        json={"action": "approve", "reviewer": "sarah"},
    )
    assert response.status_code == 404


def test_list_dlq_returns_expected_shape(client):
    response = client.get("/api/v1/dlq")
    assert response.status_code == 200
    assert "items" in response.json()


def test_replay_dlq_requires_operator_role(client):
    app = create_app()
    app.dependency_overrides[get_settings] = get_test_settings
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    async def override_db():
        yield AsyncMock()
    app.dependency_overrides[get_db_session] = override_db
    # deliberately NOT overriding require_operator_role here

    with TestClient(app) as no_role_client:
        response = no_role_client.post("/api/v1/dlq/evt-123/replay")
        assert response.status_code in (401, 403)


def test_replay_dlq_succeeds_with_operator_role(client):
    response = client.post("/api/v1/dlq/evt-123/replay")
    assert response.status_code == 200
    assert response.json()["event_id"] == "evt-123"


def test_eval_run_returns_queued_status(client):
    response = client.post("/api/v1/eval/run", json={"dataset_id": "ds-1"})
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_eval_results_returns_expected_shape(client):
    response = client.get("/api/v1/eval/results")
    assert response.status_code == 200
    assert "items" in response.json()
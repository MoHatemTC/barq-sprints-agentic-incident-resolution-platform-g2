import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.api.app import create_app
from src.api.dependencies import get_settings, get_redis, get_db_session
from src.api.schemas import Settings

TEST_TOKEN = "test-token-123"

def get_test_settings() -> Settings:
    return Settings(
        postgres_host="localhost", postgres_port=5432,
        postgres_user="test", postgres_password="test", postgres_db="test",
        redis_host="localhost", redis_port=6379,
        webhook_auth_token=TEST_TOKEN,
    )

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.rpush = AsyncMock()
    return redis

@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session

@pytest.fixture
def client(mock_redis, mock_db):
    app = create_app()
    app.dependency_overrides[get_settings] = get_test_settings
    app.dependency_overrides[get_redis] = lambda: mock_redis

    async def override_db():
        yield mock_db
    app.dependency_overrides[get_db_session] = override_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


VALID_PAYLOAD = {
    "event_id": "evt_001",
    "sys_id": "a1b2c3d4e5f678901234567890abcdef",
    "number": "INC0012345",
    "event_type": "incident.created",
    "contract_version": "v1",
}

def auth_header(token=TEST_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_webhook_happy_path_returns_202(client, mock_redis, mock_db):
    response = client.post("/webhook", json=VALID_PAYLOAD, headers=auth_header())

    assert response.status_code == 202
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()
    mock_redis.rpush.assert_awaited_once()


def test_webhook_missing_auth_header_returns_401(client, mock_redis, mock_db):
    response = client.post("/webhook", json=VALID_PAYLOAD)

    assert response.status_code == 401
    mock_redis.rpush.assert_not_awaited()
    mock_db.commit.assert_not_awaited()


def test_webhook_wrong_token_returns_401(client, mock_redis, mock_db):
    response = client.post("/webhook", json=VALID_PAYLOAD, headers=auth_header("wrong-token"))

    assert response.status_code == 401
    mock_redis.rpush.assert_not_awaited()


def test_webhook_unsupported_contract_version_returns_422(client, mock_redis, mock_db):
    payload = {**VALID_PAYLOAD, "contract_version": "v2"}
    response = client.post("/webhook", json=payload, headers=auth_header())

    assert response.status_code == 422
    mock_db.commit.assert_not_awaited()
    mock_redis.rpush.assert_not_awaited()


def test_webhook_missing_required_field_returns_422(client, mock_redis, mock_db):
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "event_id"}
    response = client.post("/webhook", json=payload, headers=auth_header())

    assert response.status_code == 422


def test_webhook_duplicate_event_returns_202_without_enqueue(client, mock_redis, mock_db):
    mock_db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate key"))

    response = client.post("/webhook", json=VALID_PAYLOAD, headers=auth_header())

    assert response.status_code == 202
    mock_db.rollback.assert_awaited_once()
    mock_redis.rpush.assert_not_awaited()  # never reached, since insert failed before enqueue
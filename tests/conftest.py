import importlib
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import text

from src.db.approval_service import create_approval
from src.db.database import SessionLocal
from src.db.models import Approval, Execution


# Modules that need live Postgres / Redis / Qdrant (or downloaded models).
# CI runs them in the integration job, which has those services; the unit job
# runs everything else with `-m "not integration"`.
_INTEGRATION_MODULES = {
    "test_act_node_registry_integration",
    "test_agent_bootstrap",
    "test_app_wiring",
    "test_approval_service",
    "test_approvals_api",
    "test_api_auth_coverage",
    "test_audit_service",
    "test_dashboard_incident_list",
    "test_dashboard_run_history",
    "test_database",
    "test_delivery_sweep",
    "test_embedding",
    "test_endpoints_contract",
    "test_event_service",
    "test_execution_service",
    "test_failure_service",
    "test_hybrid_search",
    "test_idempotency",
    "test_ingest",
    "test_knowledge_capture_service",
    "test_loop_closure",
    "test_operator_login",
    "test_permissions",
    "test_referential_integrity",
    "test_registry_enforcement",
    "test_retry_service",
    "test_state_manager",
    "test_webhook",
    "test_worker_runtime_integration",
    "test_workflow",
    "test_workflow_service",
    "test_workflow_servicenow",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in _INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)


class _StubLLM:
    def __init__(self, reply):
        self.reply = reply

    def invoke(self, prompt, **kwargs):
        return SimpleNamespace(content=self.reply)


# One fixed reply per agent: a valid ticket whose cited draft passes the critic.
_HERMETIC_REPLIES = {
    "validate": "valid",
    "classify": "network",
    "diagnose": json.dumps({
        "root_cause": "Cached VPN credentials are stale.",
        "reasoning": "KB0001 describes the same failure.",
        "supporting_evidence": ["KB0001"],
        "confidence": 0.9,
    }),
    "generate": "1. Reset the VPN credentials. [Source: KB0001]",
    "verify_evidence": json.dumps({
        "passed": True, "feedback": "", "invalid_steps": [], "citation_findings": [],
    }),
}


@pytest.fixture
def hermetic_llm(monkeypatch):
    from src.agent import llm

    monkeypatch.setattr(llm, "_llm_instance", llm.MockLLM())
    for node, reply in _HERMETIC_REPLIES.items():
        monkeypatch.setattr(f"src.agent.nodes.{node}.get_llm", lambda r=reply: _StubLLM(r))


@pytest.fixture(autouse=True)
def _reload_embedding_module_after_test():
    """Undo any monkeypatch+reload pollution left behind by tests that
    reload src.config / src.retrieval.embedding with fake env vars
    (e.g. tests/test_embedding.py). Reloads only when a test actually swapped
    the config or sparse model: rebuilding BM25 after every test cost minutes.
    """
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
    before = (cfg_mod.EMBEDDING, cfg_mod.RETRIEVAL, emb_mod._sparse_model)
    yield
    after = (cfg_mod.EMBEDDING, cfg_mod.RETRIEVAL, emb_mod._sparse_model)
    if any(a is not b for a, b in zip(after, before)):
        importlib.reload(cfg_mod)
        importlib.reload(emb_mod)


_CONSUME_AWARE_TRIGGER = """
DROP TRIGGER IF EXISTS approval_immutable ON approvals;

CREATE OR REPLACE FUNCTION prevent_approval_update() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.consumed = true AND OLD.consumed = false AND
       NEW.execution_reference IS NOT DISTINCT FROM OLD.execution_reference AND
       NEW.evidence_presented IS NOT DISTINCT FROM OLD.evidence_presented AND
       NEW.reviewer_decision IS NOT DISTINCT FROM OLD.reviewer_decision AND
       NEW.decision_timestamp IS NOT DISTINCT FROM OLD.decision_timestamp AND
       NEW.reviewer_identity IS NOT DISTINCT FROM OLD.reviewer_identity
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'approval records are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_immutable
BEFORE UPDATE OR DELETE ON approvals
FOR EACH ROW EXECUTE FUNCTION prevent_approval_update();
"""


@pytest.fixture(scope="module")
def consumed_aware_trigger():
    """Install the permissive consume-only trigger so HIGH_RISK approvals can
    be atomically checked-and-consumed by tests, mirroring the live DB schema.
    """
    db = SessionLocal()
    try:
        db.execute(text(_CONSUME_AWARE_TRIGGER))
        db.commit()
    finally:
        db.close()


def unique_execution_id(prefix):
    return f"{prefix}-{uuid4().hex}"


def create_approved_approval(execution_id):
    db = SessionLocal()
    try:
        db.add(
            Execution(
                execution_identifier=execution_id,
                incident_reference="INC-REG-TEST",
                status="started",
                agent_version="v1",
                model_name="test-model",
            )
        )
        db.commit()
        create_approval(db, execution_id, "evidence", "approved", "reviewer_user")
    finally:
        db.close()


def cleanup(execution_id):
    """Delete the approval row (consumed or not) and its execution row for
    the given execution_id, toggling the immutability trigger on/off.
    """
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE approvals DISABLE TRIGGER approval_immutable"))
        db.query(Approval).filter(
            Approval.execution_reference == execution_id
        ).delete(synchronize_session=False)
        db.query(Execution).filter(
            Execution.execution_identifier == execution_id
        ).delete(synchronize_session=False)
        db.commit()
        db.execute(text("ALTER TABLE approvals ENABLE TRIGGER approval_immutable"))
        db.commit()
    finally:
        db.close()


# Test constants for operator auth
TEST_OPERATOR_PASSWORD = "test-operator-password-123"
TEST_OPERATOR_JWT_SECRET = "test-operator-jwt-secret-min-32-chars-long"


def _operator_token(operator_jwt_secret: str, role: str = "operator", exp_delta: int = 3600) -> str:
    """Mint a JWT signed with the operator_jwt_secret (HS256)."""
    payload = {"role": role, "exp": int(time.time()) + exp_delta}
    return jwt.encode(payload, operator_jwt_secret, algorithm="HS256")


def operator_headers(operator_jwt_secret: str = TEST_OPERATOR_JWT_SECRET) -> dict:
    """Return Authorization headers with a valid operator JWT."""
    return {"Authorization": f"Bearer {_operator_token(operator_jwt_secret)}"}


def non_operator_headers(operator_jwt_secret: str = TEST_OPERATOR_JWT_SECRET) -> dict:
    """Return Authorization headers with a valid JWT but non-operator role."""
    return {"Authorization": f"Bearer {_operator_token(operator_jwt_secret, role='viewer')}"}


@pytest.fixture
def test_settings():
    """Test settings with defaults for auth and CORS."""
    from src.api.schemas import Settings
    return Settings(
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


@pytest.fixture
def api_client(test_settings):
    """Main FastAPI app with test settings and common overrides.
    
    Does NOT override require_operator_role - tests must send valid operator JWT.
    Does NOT override verify_token - tests must send valid webhook token for ServiceNow routes.
    """
    from src.api.app import create_app
    from src.api.dependencies import get_settings, get_redis, get_db_session, get_sync_db
    from src.api.auth import require_operator_role
    from src.api.routers import approvals
    from langgraph.checkpoint.memory import MemorySaver
    from src.agent.graph import create_graph
    from unittest.mock import AsyncMock, MagicMock
    from fastapi.testclient import TestClient

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

    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

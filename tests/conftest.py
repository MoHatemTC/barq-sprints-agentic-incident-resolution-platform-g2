import importlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.approval_service import create_approval
from src.db.database import SessionLocal
from src.db.models import Approval, Execution


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
    (e.g. tests/test_embedding.py). Runs after every test.
    """
    yield
    import src.config as cfg_mod
    import src.retrieval.embedding as emb_mod
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

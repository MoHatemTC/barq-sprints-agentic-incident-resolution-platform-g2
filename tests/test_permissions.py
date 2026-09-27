"""Tests for the permission gate (permissions.py) against the real Postgres."""

import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.agent.tools.permissions import PermissionClass, is_approved
from src.db.approval_service import create_approval
from src.db.database import SessionLocal
from src.db.models import Approval, Execution

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


@pytest.fixture(scope="module", autouse=True)
def consumed_aware_trigger():
    db = SessionLocal()
    try:
        db.execute(text(_CONSUME_AWARE_TRIGGER))
        db.commit()
    finally:
        db.close()


def unique_execution_id(prefix):
    return f"{prefix}-{uuid4().hex}"


def create_execution(db, execution_id):
    execution = Execution(
        execution_identifier=execution_id,
        incident_reference="INC-PERM-TEST",
        status="started",
        agent_version="v1",
        model_name="test-model",
    )
    db.add(execution)
    db.commit()
    return execution


def cleanup(execution_id):
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


def unconsumed_approval(execution_id):
    db = SessionLocal()
    try:
        create_execution(db, execution_id)
        create_approval(
            db,
            execution_id,
            "evidence",
            "approved",
            "reviewer_user",
        )
    finally:
        db.close()


def consumed_state(execution_id):
    db = SessionLocal()
    try:
        approval = (
            db.query(Approval)
            .filter(Approval.execution_reference == execution_id)
            .one()
        )
        return approval.consumed
    finally:
        db.close()


def test_read_and_low_risk_write_never_need_approval():
    assert is_approved("anything", PermissionClass.READ) is True
    assert is_approved("anything", PermissionClass.LOW_RISK_WRITE) is True


def test_high_risk_refused_when_no_approval_record_exists():
    assert is_approved(unique_execution_id("perm-none"), PermissionClass.HIGH_RISK) is False


def test_unknown_permission_class_fails_closed():
    assert is_approved("x", "not-a-permission") is False


def test_high_risk_missing_execution_id_fails_closed():
    assert is_approved(None, PermissionClass.HIGH_RISK) is False
    assert is_approved("", PermissionClass.HIGH_RISK) is False


def test_high_risk_approved_then_consumed():
    execution_id = unique_execution_id("perm-consume")
    try:
        unconsumed_approval(execution_id)
        assert is_approved(execution_id, PermissionClass.HIGH_RISK) is True
        assert is_approved(execution_id, PermissionClass.HIGH_RISK) is False
        assert consumed_state(execution_id) is True
    finally:
        cleanup(execution_id)


def test_high_risk_rejected_review_is_refused_and_not_consumed():
    execution_id = unique_execution_id("perm-reject")
    db = SessionLocal()
    try:
        create_execution(db, execution_id)
        create_approval(db, execution_id, "evidence", "rejected", "reviewer_user")
    finally:
        db.close()
    try:
        assert is_approved(execution_id, PermissionClass.HIGH_RISK) is False
        assert consumed_state(execution_id) is False
    finally:
        cleanup(execution_id)


def test_high_risk_concurrent_dispatches_consume_once():
    execution_id = unique_execution_id("perm-race")
    try:
        unconsumed_approval(execution_id)
        results = []

        def worker():
            results.append(is_approved(execution_id, PermissionClass.HIGH_RISK))

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == 1
        assert consumed_state(execution_id) is True
    finally:
        cleanup(execution_id)
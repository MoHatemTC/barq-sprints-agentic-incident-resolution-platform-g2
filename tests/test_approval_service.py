from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.database import SessionLocal
from src.db.approval_service import (
    create_approval,
    get_approvals,
)
from src.db.models import Approval, Execution


def unique_execution_reference(prefix):
    return f"{prefix}-{uuid4().hex}"


def create_test_execution(db, execution_reference):
    execution = Execution(
        execution_identifier=execution_reference,
        incident_reference="INC-TEST-001",
        status="started",
        agent_version="v1",
        model_name="test-model",
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)

    return execution


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        # Approval records are intentionally immutable during normal
        # application operation. Disable the trigger only for test cleanup.
        db.execute(
            text(
                "ALTER TABLE approvals "
                "DISABLE TRIGGER approval_immutable"
            )
        )

        db.query(Approval).filter(
            Approval.execution_reference == execution_reference
        ).delete(
            synchronize_session=False
        )

        db.execute(
            text(
                "ALTER TABLE approvals "
                "ENABLE TRIGGER approval_immutable"
            )
        )

        db.query(Execution).filter(
            Execution.execution_identifier == execution_reference
        ).delete(
            synchronize_session=False
        )

        db.commit()

    except Exception:
        db.rollback()

        # Make sure the trigger is enabled if cleanup itself fails.
        try:
            db.execute(
                text(
                    "ALTER TABLE approvals "
                    "ENABLE TRIGGER approval_immutable"
                )
            )
            db.commit()
        except Exception:
            db.rollback()

        raise

    finally:
        db.close()


def test_approval_is_created():

    execution_reference = unique_execution_reference(
        "exec-approval-001"
    )

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        approval = create_approval(
            db,
            execution_reference,
            "Incident evidence collected",
            "approved",
            "reviewer_user",
        )

        assert approval is not None
        assert approval.execution_reference == execution_reference
        assert approval.reviewer_decision == "approved"
        assert approval.reviewer_identity == "reviewer_user"

    finally:
        db.close()
        cleanup(execution_reference)


def test_approvals_are_retrieved():

    execution_reference = unique_execution_reference(
        "exec-approval-002"
    )

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        create_approval(
            db,
            execution_reference,
            "Evidence package",
            "rejected",
            "reviewer_user",
        )

        approvals = get_approvals(
            db,
            execution_reference,
        )

        assert len(approvals) == 1
        assert approvals[0].reviewer_decision == "rejected"

    finally:
        db.close()
        cleanup(execution_reference)


def test_approval_cannot_be_overwritten():

    execution_reference = unique_execution_reference(
        "exec-approval-003"
    )

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        approval = create_approval(
            db,
            execution_reference,
            "Original evidence",
            "approved",
            "reviewer_user",
        )

        approval.reviewer_decision = "rejected"

        with pytest.raises(Exception):
            db.commit()

        db.rollback()

        stored = db.query(Approval).filter(
            Approval.execution_reference == execution_reference
        ).one()

        assert stored.reviewer_decision == "approved"

    finally:
        db.rollback()
        db.close()
        cleanup(execution_reference)


def test_approval_cannot_be_deleted():

    execution_reference = unique_execution_reference(
        "exec-approval-004"
    )

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        approval = create_approval(
            db,
            execution_reference,
            "Original evidence",
            "approved",
            "reviewer_user",
        )

        db.delete(approval)

        with pytest.raises(Exception):
            db.commit()

        db.rollback()

        stored = db.query(Approval).filter(
            Approval.execution_reference == execution_reference
        ).one()

        assert stored.reviewer_decision == "approved"
        assert stored.reviewer_identity == "reviewer_user"

    finally:
        db.rollback()
        db.close()
        cleanup(execution_reference)
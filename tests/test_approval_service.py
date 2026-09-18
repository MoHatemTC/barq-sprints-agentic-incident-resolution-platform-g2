import pytest

from src.db.database import SessionLocal
from src.db.approval_service import (
    create_approval,
    get_approvals,
)
from src.db.models import Approval


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        db.query(Approval).filter(
            Approval.execution_reference == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def test_approval_is_created():

    execution_reference = "exec-approval-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
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

    execution_reference = "exec-approval-002"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
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

    execution_reference = "exec-approval-003"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
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
        db.close()
        cleanup(execution_reference)
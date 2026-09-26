from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import Approval


def create_approval(
    db: Session,
    execution_reference: str,
    evidence_presented: str,
    reviewer_decision: str,
    reviewer_identity: str,
    human_solution: str | None = None,
):
    """
    Create an approval record.
    """

    approval = Approval(
        execution_reference=execution_reference,
        evidence_presented=evidence_presented,
        human_solution=human_solution,
        reviewer_decision=reviewer_decision,
        decision_timestamp=datetime.now(timezone.utc),
        reviewer_identity=reviewer_identity,
    )

    db.add(approval)
    db.commit()
    db.refresh(approval)

    return approval


def get_approvals(
    db: Session,
    execution_reference: str,
):
    """
    Get approvals for an execution.
    """

    return (
        db.query(Approval)
        .filter(
            Approval.execution_reference == execution_reference
        )
        .all()
    )
from sqlalchemy.orm import Session

from src.db.models import WorkflowState


def save_checkpoint(
    db: Session,
    execution_reference: str,
    checkpoint: str,
):
    """
    Save workflow checkpoint.

    checkpoint is stored as text (usually JSON).
    """

    state = WorkflowState(
        execution_reference=execution_reference,
        checkpoint=checkpoint,
    )

    db.add(state)
    db.commit()
    db.refresh(state)

    return state


def get_latest_checkpoint(
    db: Session,
    execution_reference: str,
):
    """
    Get the latest checkpoint for an execution.
    """

    return (
        db.query(WorkflowState)
        .filter(
            WorkflowState.execution_reference == execution_reference
        )
        .order_by(
            WorkflowState.created_at.desc()
        )
        .first()
    )
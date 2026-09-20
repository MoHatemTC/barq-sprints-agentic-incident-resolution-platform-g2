from sqlalchemy.orm import Session

from src.db.models import WorkflowState


def save_checkpoint(
    db: Session,
    execution_reference: str,
    node_name: str,
    checkpoint: str,
):
    """
    Save workflow checkpoint.

    node_name identifies the workflow node that produced
    the checkpoint. It is stored separately so audit queries
    do not need to parse the checkpoint JSON.
    """

    state = WorkflowState(
        execution_reference=execution_reference,
        node_name=node_name,
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
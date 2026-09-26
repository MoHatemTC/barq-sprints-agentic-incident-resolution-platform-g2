import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import Execution


TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "blocked",
    "abandoned",
}


def create_execution(
    db: Session,
    incident_reference: str,
    agent_version: str = None,
    model_name: str = None,
):
    """
    Creates a new AI execution record.

    Returns:
        Execution object
    """

    execution = Execution(
        execution_identifier=str(uuid.uuid4()),
        incident_reference=incident_reference,
        status="started",
        agent_version=agent_version,
        model_name=model_name,
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)

    return execution

def get_execution(
    db: Session,
    execution_identifier: str,
):
    """
    Return an execution by its identifier.

    Returns:
        Execution object if found
        None otherwise
    """

    return (
        db.query(Execution)
        .filter(
            Execution.execution_identifier == execution_identifier
        )
        .first()
    )
def update_execution_status(
    db: Session,
    execution_identifier: str,
    status: str,
):
    """
    Update the status of an existing execution.

    Terminal statuses also receive an ended_at timestamp.
    """

    execution = (
        db.query(Execution)
        .filter(
            Execution.execution_identifier == execution_identifier
        )
        .first()
    )

    if execution is None:
        return None

    execution.status = status

    if status in TERMINAL_STATUSES:
        execution.ended_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(execution)

    return execution
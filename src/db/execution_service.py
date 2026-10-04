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
    total_tokens_in: int | None = None,
    total_tokens_out: int | None = None,
    estimated_cost_usd: float | None = None,
):
    """
    Update the status of an existing execution.

    Terminal statuses also receive an ended_at timestamp.
    Optionally persists LLM cost/token totals when provided.
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

    if total_tokens_in is not None:
        execution.total_tokens_in = (execution.total_tokens_in or 0) + total_tokens_in
    if total_tokens_out is not None:
        execution.total_tokens_out = (execution.total_tokens_out or 0) + total_tokens_out
    if estimated_cost_usd is not None:
        execution.estimated_cost_usd = (execution.estimated_cost_usd or 0.0) + estimated_cost_usd

    db.commit()
    db.refresh(execution)

    return execution
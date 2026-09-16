import uuid

from sqlalchemy.orm import Session

from src.db.models import Execution


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
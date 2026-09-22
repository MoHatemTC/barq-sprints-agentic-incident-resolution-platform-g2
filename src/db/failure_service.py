from sqlalchemy.orm import Session

from src.db.models import Failure


def record_failure(
    db: Session,
    execution_reference: str,
    failing_node: str,
    error_class: str,
    message: str,
    retry_count: int = 0,
):
    """
    Store workflow failure information.
    """

    failure = Failure(
        execution_reference=execution_reference,
        failing_node=failing_node,
        error_class=error_class,
        message=message,
        retry_count=retry_count,
    )

    db.add(failure)
    db.commit()
    db.refresh(failure)

    return failure


def get_failures(
    db: Session,
    execution_reference: str,
):
    """
    Get all failures for an execution.
    """

    return (
        db.query(Failure)
        .filter(
            Failure.execution_reference == execution_reference
        )
        .all()
    )
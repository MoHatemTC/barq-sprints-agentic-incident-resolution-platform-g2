from datetime import datetime

from sqlalchemy.orm import Session

from src.db.models import RetryState


def create_retry_state(
    db: Session,
    execution_reference: str,
    attempt_count: int,
    last_error: str = None,
    next_attempt_time: datetime = None,
):
    """
    Create retry tracking state.
    """

    retry_state = RetryState(
        execution_reference=execution_reference,
        attempt_count=attempt_count,
        last_error=last_error,
        next_attempt_time=next_attempt_time,
    )

    db.add(retry_state)
    db.commit()
    db.refresh(retry_state)

    return retry_state


def get_retry_state(
    db: Session,
    execution_reference: str,
):
    """
    Get retry states for an execution.
    """

    return (
        db.query(RetryState)
        .filter(
            RetryState.execution_reference == execution_reference
        )
        .all()
    )


def update_retry_state(
    db: Session,
    retry_state_id: int,
    attempt_count: int,
    last_error: str = None,
    next_attempt_time: datetime = None,
):
    """
    Update an existing retry state.
    """

    retry_state = (
        db.query(RetryState)
        .filter(
            RetryState.id == retry_state_id
        )
        .first()
    )

    if retry_state is None:
        return None

    retry_state.attempt_count = attempt_count
    retry_state.last_error = last_error
    retry_state.next_attempt_time = next_attempt_time

    db.commit()
    db.refresh(retry_state)

    return retry_state
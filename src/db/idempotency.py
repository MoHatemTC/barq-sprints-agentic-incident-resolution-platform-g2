from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.db.models import IdempotencyKey


def check_and_create_idempotency_key(
    db: Session,
    event_identifier: str
) -> bool:
    """
    Returns:
        True  -> first time seeing this event
        False -> duplicate event
    """

    idempotency_key = IdempotencyKey(
        event_identifier=event_identifier
    )

    db.add(idempotency_key)

    try:
        db.commit()
        return True

    except IntegrityError:
        db.rollback()
        return False
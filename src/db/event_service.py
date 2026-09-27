from sqlalchemy.orm import Session

from src.db.models import Event
from src.db.idempotency import check_and_create_idempotency_key


def process_event(
    db: Session,
    event_identifier: str,
    incident_sys_id: str,
    incident_number: str,
    event_type: str,
    contract_version: str,
):
    """
    Process incoming event.

    Returns:
        Event object if accepted
        None if duplicate
    """

    accepted = check_and_create_idempotency_key(
        db,
        event_identifier
    )

    if not accepted:
        return None

    event = Event(
        event_identifier=event_identifier,
        incident_sys_id=incident_sys_id,
        incident_number=incident_number,
        event_type=event_type,
        contract_version=contract_version,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return event


def get_incident_sys_id(
    db: Session,
    incident_number: str,
) -> str | None:
    """
    Return the ServiceNow sys_id for an incident number.

    The latest event is used when the same incident
    has generated multiple events.
    """

    event = (
        db.query(Event)
        .filter(
            Event.incident_number == incident_number
        )
        .order_by(Event.id.desc())
        .first()
    )

    if event is None:
        return None

    return event.incident_sys_id
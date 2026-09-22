from src.db.database import SessionLocal
from src.db.event_service import process_event
from src.db.models import Event, IdempotencyKey


def cleanup(identifier):
    db = SessionLocal()

    try:
        db.query(Event).filter(
            Event.event_identifier == identifier
        ).delete()

        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == identifier
        ).delete()

        db.commit()

    finally:
        db.close()


def test_new_event_is_created():

    identifier = "event-service-001"

    cleanup(identifier)

    db = SessionLocal()

    try:
        event = process_event(
            db,
            identifier,
            "incident-123",
            "INC00123",
            "incident.updated",
            "v1"
        )

        assert event is not None
        assert event.event_identifier == identifier

    finally:
        db.close()
        cleanup(identifier)


def test_duplicate_event_is_not_created():

    identifier = "event-service-002"

    cleanup(identifier)

    db = SessionLocal()

    try:
        first = process_event(
            db,
            identifier,
            "incident-123",
            "INC00123",
            "incident.updated",
            "v1"
        )

        second = process_event(
            db,
            identifier,
            "incident-123",
            "INC00123",
            "incident.updated",
            "v1"
        )

        assert first is not None
        assert second is None

    finally:
        db.close()
        cleanup(identifier)
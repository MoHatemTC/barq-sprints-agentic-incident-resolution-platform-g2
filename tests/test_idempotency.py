from src.db.database import SessionLocal
from src.db.models import IdempotencyKey
from src.db.idempotency import check_and_create_idempotency_key


def cleanup_event(event_identifier):
    db = SessionLocal()

    try:
        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).delete()

        db.commit()

    finally:
        db.close()


def test_first_event_is_accepted():

    cleanup_event("event-test-001")

    db = SessionLocal()

    try:
        result = check_and_create_idempotency_key(
            db,
            "event-test-001"
        )

        assert result is True

    finally:
        db.close()
        cleanup_event("event-test-001")


def test_duplicate_event_is_rejected():

    cleanup_event("event-test-002")

    db = SessionLocal()

    try:
        first = check_and_create_idempotency_key(
            db,
            "event-test-002"
        )

        second = check_and_create_idempotency_key(
            db,
            "event-test-002"
        )

        assert first is True
        assert second is False

    finally:
        db.close()
        cleanup_event("event-test-002")
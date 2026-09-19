import threading

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

    event_identifier = "event-test-001"

    cleanup_event(event_identifier)

    db = SessionLocal()

    try:
        result = check_and_create_idempotency_key(
            db,
            event_identifier,
        )

        assert result is True

    finally:
        db.close()
        cleanup_event(event_identifier)


def test_duplicate_event_is_rejected():

    event_identifier = "event-test-002"

    cleanup_event(event_identifier)

    db = SessionLocal()

    try:
        first = check_and_create_idempotency_key(
            db,
            event_identifier,
        )

        second = check_and_create_idempotency_key(
            db,
            event_identifier,
        )

        assert first is True
        assert second is False

    finally:
        db.close()
        cleanup_event(event_identifier)


def test_concurrent_event_is_accepted_only_once():

    event_identifier = "event-concurrent-001"

    cleanup_event(event_identifier)

    results = []
    errors = []

    barrier = threading.Barrier(2)

    def worker():
        db = SessionLocal()

        try:
            # Make both workers reach the insert at approximately
            # the same time.
            barrier.wait()

            result = check_and_create_idempotency_key(
                db,
                event_identifier,
            )

            results.append(result)

        except Exception as error:
            errors.append(error)

        finally:
            db.close()

    worker_a = threading.Thread(target=worker)
    worker_b = threading.Thread(target=worker)

    worker_a.start()
    worker_b.start()

    worker_a.join()
    worker_b.join()

    try:
        assert errors == []

        # Exactly one worker must win.
        assert results.count(True) == 1

        # Exactly one worker must be rejected.
        assert results.count(False) == 1

        # The database must contain exactly one key.
        db = SessionLocal()

        try:
            keys = (
                db.query(IdempotencyKey)
                .filter(
                    IdempotencyKey.event_identifier
                    == event_identifier
                )
                .all()
            )

            assert len(keys) == 1

        finally:
            db.close()

    finally:
        cleanup_event(event_identifier)
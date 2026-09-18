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

    cleanup_event("event-test-001")

    db = SessionLocal()

    try:
        result = check_and_create_idempotency_key(
            db,
            "event-test-001",
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
            "event-test-002",
        )

        second = check_and_create_idempotency_key(
            db,
            "event-test-002",
        )

        assert first is True
        assert second is False

    finally:
        db.close()
        cleanup_event("event-test-002")


def test_concurrent_replay_allows_only_one_worker():

    event_identifier = "event-concurrent-001"

    cleanup_event(event_identifier)

    results = []
    errors = []

    barrier = threading.Barrier(2)

    def worker():
        db = SessionLocal()

        try:
            # Make both workers reach the INSERT at approximately
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
        # Neither worker should crash.
        assert errors == []

        # Exactly one worker accepts the event.
        assert results.count(True) == 1

        # Exactly one worker rejects the duplicate.
        assert results.count(False) == 1

        # The database must contain exactly one key.
        db = SessionLocal()

        try:
            keys = db.query(IdempotencyKey).filter(
                IdempotencyKey.event_identifier == event_identifier
            ).all()

            assert len(keys) == 1

        finally:
            db.close()

    finally:
        cleanup_event(event_identifier)
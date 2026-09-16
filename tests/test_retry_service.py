from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.retry_service import (
    create_retry_state,
    get_retry_state,
)
from src.db.models import RetryState


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        db.query(RetryState).filter(
            RetryState.execution_reference == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def test_retry_state_is_created():

    execution_reference = "exec-retry-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        retry = create_retry_state(
            db,
            execution_reference,
            attempt_count=1,
            last_error="TimeoutError",
            next_attempt_time=datetime.now(timezone.utc),
        )

        assert retry is not None
        assert retry.execution_reference == execution_reference
        assert retry.attempt_count == 1
        assert retry.last_error == "TimeoutError"

    finally:
        db.close()
        cleanup(execution_reference)


def test_retry_state_is_retrieved():

    execution_reference = "exec-retry-002"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_retry_state(
            db,
            execution_reference,
            attempt_count=2,
            last_error="Connection failed",
        )

        retries = get_retry_state(
            db,
            execution_reference
        )

        assert len(retries) == 1
        assert retries[0].attempt_count == 2
        assert retries[0].last_error == "Connection failed"

    finally:
        db.close()
        cleanup(execution_reference)
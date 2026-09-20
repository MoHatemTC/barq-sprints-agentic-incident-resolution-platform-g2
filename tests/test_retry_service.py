from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.retry_service import (
    create_retry_state,
    get_retry_state,
    update_retry_state,
)
from src.db.models import Execution, RetryState


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        db.query(RetryState).filter(
            RetryState.execution_reference == execution_reference
        ).delete()

        db.query(Execution).filter(
            Execution.execution_identifier == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def create_test_execution(db, execution_reference):

    execution = Execution(
        execution_identifier=execution_reference,
        incident_reference=f"INC-{execution_reference}",
        status="started",
        agent_version="v1",
        model_name="test-model",
        started_at=datetime.now(timezone.utc),
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)

    return execution


def test_retry_state_is_created():

    execution_reference = "exec-retry-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

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
        create_test_execution(
            db,
            execution_reference,
        )

        create_retry_state(
            db,
            execution_reference,
            attempt_count=2,
            last_error="Connection failed",
        )

        retries = get_retry_state(
            db,
            execution_reference,
        )

        assert len(retries) == 1
        assert retries[0].attempt_count == 2
        assert retries[0].last_error == "Connection failed"

    finally:
        db.close()
        cleanup(execution_reference)


def test_retry_state_is_updated():

    execution_reference = "exec-retry-003"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        retry = create_retry_state(
            db,
            execution_reference,
            attempt_count=1,
            last_error="TimeoutError",
        )

        updated = update_retry_state(
            db,
            retry.id,
            attempt_count=2,
            last_error="Connection failed",
        )

        assert updated is not None
        assert updated.id == retry.id
        assert updated.execution_reference == execution_reference
        assert updated.attempt_count == 2
        assert updated.last_error == "Connection failed"

    finally:
        db.close()
        cleanup(execution_reference)
from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.failure_service import (
    record_failure,
    get_failures,
)
from src.db.models import Execution, Failure


def create_test_execution(db, execution_reference):

    execution = Execution(
        execution_identifier=execution_reference,
        incident_reference="INC-TEST-001",
        status="started",
        agent_version="v1",
        model_name="test-model",
        started_at=datetime.now(timezone.utc),
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)

    return execution


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        # Delete child records first because Failure references Execution.
        db.query(Failure).filter(
            Failure.execution_reference == execution_reference
        ).delete(
            synchronize_session=False
        )

        # Then delete the parent execution.
        db.query(Execution).filter(
            Execution.execution_identifier == execution_reference
        ).delete(
            synchronize_session=False
        )

        db.commit()

    finally:
        db.close()


def test_failure_is_recorded():

    execution_reference = "exec-failure-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        failure = record_failure(
            db,
            execution_reference,
            "retrieve_kb",
            "TimeoutError",
            "KB service timeout",
            retry_count=1,
        )

        assert failure is not None
        assert failure.execution_reference == execution_reference
        assert failure.failing_node == "retrieve_kb"
        assert failure.retry_count == 1

    finally:
        db.close()
        cleanup(execution_reference)


def test_failures_are_retrieved():

    execution_reference = "exec-failure-002"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        record_failure(
            db,
            execution_reference,
            "analysis_node",
            "ValueError",
            "Invalid response",
            retry_count=0,
        )

        failures = get_failures(
            db,
            execution_reference,
        )

        assert len(failures) == 1
        assert failures[0].error_class == "ValueError"

    finally:
        db.close()
        cleanup(execution_reference)
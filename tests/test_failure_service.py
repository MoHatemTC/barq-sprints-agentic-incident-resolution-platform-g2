from src.db.database import SessionLocal
from src.db.failure_service import (
    record_failure,
    get_failures,
)
from src.db.models import Failure


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        db.query(Failure).filter(
            Failure.execution_reference == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def test_failure_is_recorded():

    execution_reference = "exec-failure-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
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
            execution_reference
        )

        assert len(failures) == 1
        assert failures[0].error_class == "ValueError"

    finally:
        db.close()
        cleanup(execution_reference)
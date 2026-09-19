import threading

from src.db.database import SessionLocal
from src.db.models import IdempotencyKey, Execution
from src.orchestrator.state_manager import StateManager
from src.orchestrator.workflow import IncidentWorkflow
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


def test_concurrent_event_creates_only_one_execution():
    event_identifier = "event-concurrent-execution-001"
    incident_number = "INC-CONCURRENT-001"

    cleanup_event(event_identifier)

    # Remove any executions left by a previous test run.
    db = SessionLocal()

    try:
        db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).delete()

        db.commit()
    finally:
        db.close()

    results = []
    errors = []

    barrier = threading.Barrier(2)

    def worker():
        db = SessionLocal()

        try:
            state_manager = StateManager(db)
            workflow = IncidentWorkflow(state_manager)

            # Make both workers start at approximately the same time.
            barrier.wait()

            result = workflow.run(
                event_identifier=event_identifier,
                incident_sys_id="incident-concurrent-sys-id",
                incident_number=incident_number,
                event_type="incident.updated",
                contract_version="v1",
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

        # Exactly one worker must start the workflow.
        assert sum(
            result["status"] == "started"
            for result in results
        ) == 1

        # Exactly one worker must be rejected as a duplicate.
        assert sum(
            result["status"] == "duplicate"
            for result in results
        ) == 1

        # Verify the database contains exactly one execution.
        db = SessionLocal()

        try:
            executions = (
                db.query(Execution)
                .filter(
                    Execution.incident_reference
                    == incident_number
                )
                .all()
            )

            assert len(executions) == 1

        finally:
            db.close()

    finally:
        cleanup_event(event_identifier)

        db = SessionLocal()

        try:
            db.query(Execution).filter(
                Execution.incident_reference == incident_number
            ).delete()

            db.commit()

        finally:
            db.close()
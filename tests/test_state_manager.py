from src.db.database import SessionLocal
from src.db.models import (
    Event,
    Execution,
    WorkflowState,
    Failure,
    Approval,
    RetryState,
    IdempotencyKey,
)

from src.orchestrator.state_manager import StateManager


def cleanup(event_identifier, execution_reference):

    db = SessionLocal()

    try:
        db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).delete()

        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).delete()

        db.query(WorkflowState).filter(
            WorkflowState.execution_reference == execution_reference
        ).delete()

        db.query(Failure).filter(
            Failure.execution_reference == execution_reference
        ).delete()

        db.query(Approval).filter(
            Approval.execution_reference == execution_reference
        ).delete()

        db.query(RetryState).filter(
            RetryState.execution_reference == execution_reference
        ).delete()

        db.query(Execution).filter(
            Execution.execution_identifier == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def test_state_manager_coordinates_event_and_execution():

    event_identifier = "state-manager-event-001"
    execution_reference = "state-manager-exec-001"

    cleanup(
        event_identifier,
        execution_reference,
    )

    db = SessionLocal()

    try:
        manager = StateManager(db)

        event = manager.process_event(
            event_identifier,
            "incident-123",
            "INC00123",
            "incident.updated",
            "v1",
        )

        assert event is not None

        execution = manager.create_execution(
            incident_reference="INC00123",
            agent_version="v1",
            model_name="test-model",
        )

        assert execution is not None
        assert execution.status == "started"

        checkpoint = manager.save_checkpoint(
            execution.execution_identifier,
            "test_node",
            '{"step":1}',
        )

        assert checkpoint is not None
        assert checkpoint.node_name == "test_node"
        assert checkpoint.checkpoint == '{"step":1}'

        latest = manager.get_latest_checkpoint(
            execution.execution_identifier
        )

        assert latest is not None
        assert latest.node_name == "test_node"
        assert latest.checkpoint == '{"step":1}'

    finally:
        db.close()
        cleanup(
            event_identifier,
            execution.execution_identifier,
        )


def test_state_manager_updates_execution_status():

    db = SessionLocal()

    execution_identifier = None

    try:
        manager = StateManager(db)

        execution = manager.create_execution(
            incident_reference="INC-STATE-STATUS-001",
            agent_version="v1",
            model_name="test-model",
        )

        execution_identifier = execution.execution_identifier

        assert execution.status == "started"

        updated = manager.update_execution_status(
            execution_identifier,
            "failed",
        )

        assert updated is not None
        assert updated.execution_identifier == execution_identifier
        assert updated.status == "failed"

    finally:
        db.close()

        if execution_identifier:
            cleanup(
                "state-manager-status-event-001",
                execution_identifier,
            )
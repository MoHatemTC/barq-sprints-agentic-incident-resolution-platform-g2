from src.db.database import SessionLocal
from src.db.models import (
    Event,
    Execution,
    WorkflowState,
    IdempotencyKey,
)
from src.orchestrator.state_manager import StateManager
from src.orchestrator.workflow import IncidentWorkflow


def cleanup(event_identifier, incident_number):

    db = SessionLocal()

    try:
        executions = db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).all()

        execution_ids = [
            execution.execution_identifier
            for execution in executions
        ]

        # Delete checkpoints first.
        for execution_id in execution_ids:
            db.query(WorkflowState).filter(
                WorkflowState.execution_reference == execution_id
            ).delete()

        # Delete executions.
        db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).delete()

        # Delete event.
        db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).delete()

        # Delete idempotency key.
        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).delete()

        db.commit()

    finally:
        db.close()


def test_workflow_starts_execution():

    event_identifier = "workflow-event-001"
    incident_number = "INC-WORKFLOW-001"

    cleanup(
        event_identifier,
        incident_number,
    )

    db = SessionLocal()

    try:
        state_manager = StateManager(db)
        workflow = IncidentWorkflow(state_manager)

        result = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-001",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
        )

        assert result["status"] == "started"
        assert result["event"] is not None
        assert result["execution"] is not None

        execution = result["execution"]

        checkpoint = state_manager.get_latest_checkpoint(
            execution.execution_identifier
        )

        assert checkpoint is not None
        assert "workflow_started" in checkpoint.checkpoint

    finally:
        db.close()

        cleanup(
            event_identifier,
            incident_number,
        )


def test_duplicate_event_does_not_create_second_execution():

    event_identifier = "workflow-duplicate-001"
    incident_number = "INC-WORKFLOW-002"

    cleanup(
        event_identifier,
        incident_number,
    )

    db = SessionLocal()

    try:
        state_manager = StateManager(db)
        workflow = IncidentWorkflow(state_manager)

        # First delivery.
        first = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-002",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
        )

        # Replay of the exact same event.
        second = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-002",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
        )

        # First event must start an execution.
        assert first["status"] == "started"
        assert first["execution"] is not None

        # Replay must be rejected.
        assert second["status"] == "duplicate"
        assert second["execution"] is None

        # Exactly one event exists.
        events = db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).all()

        assert len(events) == 1

        # Exactly one idempotency key exists.
        idempotency_keys = db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).all()

        assert len(idempotency_keys) == 1

        # Exactly one execution exists.
        executions = db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).all()

        assert len(executions) == 1

        execution = executions[0]

        # Exactly one workflow checkpoint exists.
        checkpoints = db.query(WorkflowState).filter(
            WorkflowState.execution_reference
            == execution.execution_identifier
        ).all()

        assert len(checkpoints) == 1

        assert "workflow_started" in checkpoints[0].checkpoint

    finally:
        db.close()

        cleanup(
            event_identifier,
            incident_number,
        )


def test_workflow_records_failure():

    event_identifier = "workflow-failure-001"
    incident_number = "INC-WORKFLOW-FAILURE-001"

    cleanup(
        event_identifier,
        incident_number,
    )

    db = SessionLocal()

    try:
        state_manager = StateManager(db)
        workflow = IncidentWorkflow(state_manager)

        result = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-failure-001",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
            simulate_failure=True,
        )

        assert result["status"] == "failed"
        assert result["execution"] is not None
        assert result["error"] == "Simulated workflow node failure"

        execution = result["execution"]

        assert execution.status == "failed"

        failures = state_manager.get_failures(
            execution.execution_identifier
        )

        assert len(failures) == 1
        assert failures[0].failing_node == "workflow_node"
        assert failures[0].error_class == "RuntimeError"

    finally:
        db.close()

        cleanup(
            event_identifier,
            incident_number,
        )
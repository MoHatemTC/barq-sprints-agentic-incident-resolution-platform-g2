from src.db.database import SessionLocal
from src.db.models import (
    Event,
    Execution,
    WorkflowState,
    Failure,
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

        # Delete failures first because they reference executions.
        for execution_id in execution_ids:
            db.query(Failure).filter(
                Failure.execution_reference == execution_id
            ).delete(
                synchronize_session=False
            )

        # Delete workflow checkpoints because they also reference executions.
        for execution_id in execution_ids:
            db.query(WorkflowState).filter(
                WorkflowState.execution_reference == execution_id
            ).delete(
                synchronize_session=False
            )

        # Delete executions last.
        db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).delete(
            synchronize_session=False
        )

        # Delete event.
        db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).delete(
            synchronize_session=False
        )

        # Delete idempotency key.
        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).delete(
            synchronize_session=False
        )

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
        assert checkpoint.node_name == "workflow_started"
        assert checkpoint.checkpoint == '{"step":1}'

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

        first = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-002",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
        )

        second = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id="incident-sys-002",
            incident_number=incident_number,
            event_type="incident.updated",
            contract_version="v1",
        )

        assert first["status"] == "started"
        assert first["execution"] is not None

        assert second["status"] == "duplicate"
        assert second["execution"] is None

        events = db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).all()

        assert len(events) == 1

        idempotency_keys = db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).all()

        assert len(idempotency_keys) == 1

        executions = db.query(Execution).filter(
            Execution.incident_reference == incident_number
        ).all()

        assert len(executions) == 1

        execution = executions[0]

        checkpoints = db.query(WorkflowState).filter(
            WorkflowState.execution_reference
            == execution.execution_identifier
        ).all()

        assert len(checkpoints) == 1

        assert checkpoints[0].node_name == "workflow_started"
        assert checkpoints[0].checkpoint == '{"step":1}'

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
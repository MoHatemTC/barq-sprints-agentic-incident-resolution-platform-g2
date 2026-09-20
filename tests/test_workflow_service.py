from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.workflow_service import (
    save_checkpoint,
    get_latest_checkpoint,
)
from src.db.models import Execution, WorkflowState


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
        db.query(WorkflowState).filter(
            WorkflowState.execution_reference == execution_reference
        ).delete(
            synchronize_session=False
        )

        db.query(Execution).filter(
            Execution.execution_identifier == execution_reference
        ).delete(
            synchronize_session=False
        )

        db.commit()

    finally:
        db.close()


def test_checkpoint_is_saved():

    execution_reference = "exec-workflow-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        state = save_checkpoint(
            db,
            execution_reference,
            "analyze_incident",
            '{"step":1}',
        )

        assert state is not None
        assert state.execution_reference == execution_reference
        assert state.node_name == "analyze_incident"
        assert state.checkpoint == '{"step":1}'

    finally:
        db.close()
        cleanup(execution_reference)


def test_latest_checkpoint_is_returned():

    execution_reference = "exec-workflow-002"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        save_checkpoint(
            db,
            execution_reference,
            "step_1",
            '{"step":1}',
        )

        save_checkpoint(
            db,
            execution_reference,
            "step_2",
            '{"step":2}',
        )

        latest = get_latest_checkpoint(
            db,
            execution_reference,
        )

        assert latest is not None
        assert latest.node_name == "step_2"
        assert latest.checkpoint == '{"step":2}'

    finally:
        db.close()
        cleanup(execution_reference)
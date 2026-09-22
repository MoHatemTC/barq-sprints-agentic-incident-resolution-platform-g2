from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.models import Execution, WorkflowState
from src.db.workflow_service import (
    get_latest_checkpoint,
    save_checkpoint,
)


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


def create_test_execution(db, execution_reference):
    execution = Execution(
        execution_identifier=execution_reference,
        incident_reference=f"INC-{execution_reference}",
        status="started",
        started_at=datetime.now(timezone.utc),
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)

    return execution


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

        first = save_checkpoint(
            db,
            execution_reference,
            "step_1",
            '{"step":1}',
        )

        second = save_checkpoint(
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
        assert latest.id == second.id
        assert latest.node_name == "step_2"
        assert latest.checkpoint == '{"step":2}'

        assert latest.id > first.id

    finally:
        db.close()
        cleanup(execution_reference)


def test_latest_checkpoint_uses_id_as_tie_breaker():

    execution_reference = "exec-workflow-tie-breaker"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        create_test_execution(
            db,
            execution_reference,
        )

        timestamp = datetime.now(timezone.utc)

        first = WorkflowState(
            execution_reference=execution_reference,
            node_name="step_1",
            checkpoint='{"step":1}',
            created_at=timestamp,
            updated_at=timestamp,
        )

        second = WorkflowState(
            execution_reference=execution_reference,
            node_name="step_2",
            checkpoint='{"step":2}',
            created_at=timestamp,
            updated_at=timestamp,
        )

        db.add_all([first, second])
        db.commit()

        db.refresh(first)
        db.refresh(second)

        latest = get_latest_checkpoint(
            db,
            execution_reference,
        )

        assert latest is not None
        assert latest.id == second.id
        assert latest.node_name == "step_2"
        assert latest.checkpoint == '{"step":2}'

        assert second.id > first.id

    finally:
        db.close()
        cleanup(execution_reference)
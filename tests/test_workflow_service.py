from src.db.database import SessionLocal
from src.db.workflow_service import (
    save_checkpoint,
    get_latest_checkpoint,
)
from src.db.models import WorkflowState


def cleanup(execution_reference):

    db = SessionLocal()

    try:
        db.query(WorkflowState).filter(
            WorkflowState.execution_reference == execution_reference
        ).delete()

        db.commit()

    finally:
        db.close()


def test_checkpoint_is_saved():

    execution_reference = "exec-workflow-001"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        state = save_checkpoint(
            db,
            execution_reference,
            '{"node":"analyze_incident","step":1}'
        )

        assert state is not None
        assert state.execution_reference == execution_reference
        assert "analyze_incident" in state.checkpoint

    finally:
        db.close()
        cleanup(execution_reference)


def test_latest_checkpoint_is_returned():

    execution_reference = "exec-workflow-002"

    cleanup(execution_reference)

    db = SessionLocal()

    try:
        save_checkpoint(
            db,
            execution_reference,
            '{"node":"step_1"}'
        )

        save_checkpoint(
            db,
            execution_reference,
            '{"node":"step_2"}'
        )

        latest = get_latest_checkpoint(
            db,
            execution_reference
        )

        assert latest is not None
        assert "step_2" in latest.checkpoint

    finally:
        db.close()
        cleanup(execution_reference)
from src.db.database import SessionLocal
from src.db.execution_service import (
    create_execution,
    update_execution_status,
)
from src.db.models import Execution


def cleanup(execution_identifier):

    db = SessionLocal()

    try:
        db.query(Execution).filter(
            Execution.execution_identifier == execution_identifier
        ).delete()

        db.commit()

    finally:
        db.close()


def test_execution_is_created():

    db = SessionLocal()

    try:
        execution = create_execution(
            db,
            incident_reference="INC00123",
            agent_version="v1",
            model_name="test-model",
        )

        assert execution is not None
        assert execution.status == "started"
        assert execution.execution_identifier is not None

    finally:
        db.close()
        
        
def test_execution_status_is_updated():

    db = SessionLocal()

    execution_identifier = None

    try:
        execution = create_execution(
            db,
            incident_reference="INC00124",
            agent_version="v1",
            model_name="test-model",
        )

        execution_identifier = execution.execution_identifier

        updated = update_execution_status(
            db,
            execution_identifier,
            "failed",
        )

        assert updated is not None
        assert updated.execution_identifier == execution_identifier
        assert updated.status == "failed"

    finally:
        db.close()

        if execution_identifier:
            cleanup(execution_identifier)
from datetime import datetime, timedelta, timezone

import pytest

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
        ).delete(
            synchronize_session=False
        )

        db.commit()

    finally:
        db.close()


def test_execution_is_created():

    db = SessionLocal()

    execution_identifier = None

    try:
        execution = create_execution(
            db,
            incident_reference="INC00123",
            agent_version="v1",
            model_name="test-model",
        )

        execution_identifier = execution.execution_identifier

        assert execution is not None
        assert execution.status == "started"
        assert execution.execution_identifier is not None

    finally:
        db.close()

        if execution_identifier:
            cleanup(execution_identifier)


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


def test_invalid_execution_status_is_rejected():

    execution_identifier = "invalid-status-test-pytest"

    cleanup(execution_identifier)

    db = SessionLocal()

    try:
        execution = Execution(
            execution_identifier=execution_identifier,
            incident_reference="INC-INVALID-STATUS",
            status="not_a_real_status",
            started_at=datetime.now(timezone.utc),
        )

        db.add(execution)

        with pytest.raises(Exception):
            db.commit()

        db.rollback()

    finally:
        db.close()
        cleanup(execution_identifier)


def test_invalid_execution_time_order_is_rejected():

    execution_identifier = "invalid-time-test-pytest"

    cleanup(execution_identifier)

    db = SessionLocal()

    try:
        started_at = datetime.now(timezone.utc)

        execution = Execution(
            execution_identifier=execution_identifier,
            incident_reference="INC-INVALID-TIME",
            status="started",
            started_at=started_at,
            ended_at=started_at - timedelta(hours=1),
        )

        db.add(execution)

        with pytest.raises(Exception):
            db.commit()

        db.rollback()

    finally:
        db.close()
        cleanup(execution_identifier)
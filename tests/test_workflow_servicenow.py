import os
import uuid

import pytest

from src.db.database import SessionLocal
from src.db.models import (
    Event,
    Execution,
    WorkflowState,
    IdempotencyKey,
)
from src.orchestrator.state_manager import StateManager
from src.orchestrator.workflow import IncidentWorkflow
from src.servicenow.client import ServiceNowClient
from src.servicenow import config


# Talks to the live ServiceNow instance; same opt-in as tests/test_integration.py.
# CI (live-servicenow.yml) points these at the dedicated test incident.
INCIDENT_SYS_ID = os.getenv("INCIDENT_SYS_ID", "")
INCIDENT_NUMBER = os.getenv("INCIDENT_NUMBER", "")

pytestmark = pytest.mark.skipif(
    not (INCIDENT_SYS_ID and INCIDENT_NUMBER),
    reason="INCIDENT_SYS_ID / INCIDENT_NUMBER not set (live ServiceNow instance)",
)


def get_servicenow_logs(client):
    # Newest first: every run adds rows to this real incident (126 by 27 Sep), so an
    # unordered first page can leave out the rows this run just wrote.
    query = f"incident={INCIDENT_SYS_ID}^ORDERBYDESCsys_created_on"

    return client._request(
        "GET",
        config.TABLE_API + "/" + config.EXECUTION_LOG_TABLE,
        params={
            "sysparm_query": query,
            "sysparm_fields": (
                "sys_id,execution_id,action,status,"
                "agent,result,error"
            ),
            "sysparm_limit": "1000",
        },
    )


def cleanup_database(event_identifier):

    db = SessionLocal()

    try:
        executions = (
            db.query(Execution)
            .filter(
                Execution.incident_reference == INCIDENT_NUMBER
            )
            .all()
        )

        execution_ids = [
            execution.execution_identifier
            for execution in executions
        ]

        for execution_id in execution_ids:
            db.query(WorkflowState).filter(
                WorkflowState.execution_reference == execution_id
            ).delete()

        db.query(Execution).filter(
            Execution.incident_reference == INCIDENT_NUMBER
        ).delete()

        db.query(Event).filter(
            Event.event_identifier == event_identifier
        ).delete()

        db.query(IdempotencyKey).filter(
            IdempotencyKey.event_identifier == event_identifier
        ).delete()

        db.commit()

    finally:
        db.close()


def test_workflow_replay_creates_one_execution_and_no_duplicate_log():

    event_identifier = (
        "sprint2-pdi-replay-" + str(uuid.uuid4())
    )

    cleanup_database(event_identifier)

    client = ServiceNowClient()

    # Capture the existing ServiceNow logs.
    # We compare against this baseline instead of assuming
    # the incident has never been used before.
    before_logs = get_servicenow_logs(client)
    before_log_ids = {
        log["sys_id"]
        for log in before_logs
    }

    db = SessionLocal()

    try:
        state_manager = StateManager(db)

        workflow = IncidentWorkflow(
            state_manager,
            servicenow_client=client,
        )

        # ---------------------------------------------------------
        # First delivery
        # ---------------------------------------------------------

        first = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id=INCIDENT_SYS_ID,
            incident_number=INCIDENT_NUMBER,
            event_type="incident.updated",
            contract_version="v1",
        )

        assert first["status"] == "started"
        assert first["execution"] is not None

        execution_id = (
            first["execution"].execution_identifier
        )

        # ---------------------------------------------------------
        # Verify exactly one new PostgreSQL execution
        # ---------------------------------------------------------

        executions = (
            db.query(Execution)
            .filter(
                Execution.incident_reference == INCIDENT_NUMBER
            )
            .all()
        )

        assert len(executions) == 1
        assert (
            executions[0].execution_identifier
            == execution_id
        )

        # ---------------------------------------------------------
        # Verify exactly two NEW ServiceNow logs
        # ---------------------------------------------------------

        after_first_logs = get_servicenow_logs(client)

        new_first_logs = [
            log
            for log in after_first_logs
            if log["sys_id"] not in before_log_ids
        ]

        assert len(new_first_logs) == 2

        new_execution_ids = {
            log["execution_id"]
            for log in new_first_logs
        }

        assert new_execution_ids == {execution_id}

        statuses = {
            log["status"]
            for log in new_first_logs
        }

        assert statuses == {
            "started",
            "succeeded",
        }

        # ---------------------------------------------------------
        # Replay the exact same event
        # ---------------------------------------------------------

        second = workflow.run(
            event_identifier=event_identifier,
            incident_sys_id=INCIDENT_SYS_ID,
            incident_number=INCIDENT_NUMBER,
            event_type="incident.updated",
            contract_version="v1",
        )

        assert second["status"] == "duplicate"
        assert second["execution"] is None

        # ---------------------------------------------------------
        # Verify no second PostgreSQL execution
        # ---------------------------------------------------------

        executions_after_replay = (
            db.query(Execution)
            .filter(
                Execution.incident_reference == INCIDENT_NUMBER
            )
            .all()
        )

        assert len(executions_after_replay) == 1

        # ---------------------------------------------------------
        # Verify replay created NO additional ServiceNow logs
        # ---------------------------------------------------------

        after_replay_logs = get_servicenow_logs(client)

        new_replay_logs = [
            log
            for log in after_replay_logs
            if log["sys_id"] not in before_log_ids
        ]

        assert len(new_replay_logs) == 2

        replay_execution_ids = {
            log["execution_id"]
            for log in new_replay_logs
        }

        assert replay_execution_ids == {execution_id}

    finally:
        db.close()
        cleanup_database(event_identifier)
"""Dashboard-only endpoints: real data reads + convenience actions.

These are additive and do not touch the existing (stubbed) executions.py
router used by the S2.2 contract tests. This router talks to the real
SQLAlchemy sync session (src.db.database.SessionLocal) so it can show
actual rows while S2.2's async wiring lands.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, desc

from src.db.database import SessionLocal
from src.db.models import Event, Execution, Failure, RetryState, WorkflowState

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/executions")
async def list_recent_executions(limit: int = 30):
    db = SessionLocal()
    try:
        executions = (
            db.execute(
                select(Execution).order_by(desc(Execution.started_at)).limit(limit)
            )
            .scalars()
            .all()
        )

        results = []
        for execution in executions:
            failures = (
                db.execute(
                    select(Failure)
                    .where(Failure.execution_reference == execution.execution_identifier)
                    .order_by(desc(Failure.id))
                )
                .scalars()
                .all()
            )
            retry_state = (
                db.execute(
                    select(RetryState)
                    .where(RetryState.execution_reference == execution.execution_identifier)
                )
                .scalars()
                .first()
            )
            checkpoints = (
                db.execute(
                    select(WorkflowState)
                    .where(WorkflowState.execution_reference == execution.execution_identifier)
                    .order_by(WorkflowState.created_at)
                )
                .scalars()
                .all()
            )
            event = (
                db.execute(
                    select(Event)
                    .where(Event.incident_number == execution.incident_reference)
                    .order_by(desc(Event.received_at))
                )
                .scalars()
                .first()
            )

            latest_checkpoint = None
            if checkpoints:
                try:
                    latest_checkpoint = json.loads(checkpoints[-1].checkpoint)
                except (json.JSONDecodeError, TypeError):
                    latest_checkpoint = {"raw": checkpoints[-1].checkpoint}

            results.append(
                {
                    "execution_id": execution.execution_identifier,
                    "incident_number": execution.incident_reference,
                    "incident_sys_id": event.incident_sys_id if event else None,
                    "status": execution.status,
                    "node_reached": execution.node_reached,
                    "started_at": execution.started_at.isoformat() if execution.started_at else None,
                    "ended_at": execution.ended_at.isoformat() if execution.ended_at else None,
                    "duration_seconds": (
                        (execution.ended_at - execution.started_at).total_seconds()
                        if execution.ended_at and execution.started_at
                        else None
                    ),
                    "checkpoints": [
                        {
                            "node_name": getattr(cp, "node_name", None),
                            "created_at": cp.created_at.isoformat() if cp.created_at else None,
                        }
                        for cp in checkpoints
                    ],
                    "latest_result": latest_checkpoint,
                    "failures": [
                        {
                            "failing_node": f.failing_node,
                            "error_class": f.error_class,
                            "message": f.message,
                            "retry_count": f.retry_count,
                        }
                        for f in failures
                    ],
                    "retry_attempt_count": retry_state.attempt_count if retry_state else 0,
                }
            )

        return {"executions": results, "count": len(results)}
    finally:
        db.close()


def _category_choices(client) -> list[dict]:
    # The incident category list exactly as the ServiceNow form shows it
    # (sys_choice can repeat a value, e.g. per domain; keep the first).
    choices, seen = [], set()
    for choice in client.get_choices("incident", "category"):
        value = choice.get("value")
        if value and value not in seen:
            seen.add(value)
            choices.append({"value": value, "label": choice.get("label") or value})
    return choices


@router.get("/incident-categories")
def list_incident_categories():
    from src.servicenow.client import ServiceNowClient

    try:
        return {"categories": _category_choices(ServiceNowClient())}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not read categories from ServiceNow: {exc}"
        ) from exc


class NewIncidentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    short_description: str = Field(..., min_length=1)
    description: str | None = None
    category: str = Field(..., min_length=1)


@router.post("/incidents", status_code=201)
def create_incident_via_dashboard(payload: NewIncidentRequest):
    """Create an incident in ServiceNow, the same as filling in the ServiceNow form.

    Nothing is stored or queued here. The insert fires the AI Eligibility Check
    Business Rule (servicenow/ai_incident_orchestrator/update_set.xml),
    which alone decides whether the incident reaches the webhook.
    """
    import os

    from src.servicenow.client import ServiceNowClient

    client = ServiceNowClient()
    try:
        categories = {c["value"] for c in _category_choices(client)}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not read categories from ServiceNow: {exc}"
        ) from exc
    # The Table API stores any string in a choice field; the form does not.
    if payload.category not in categories:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.category}' is not an incident category in ServiceNow",
        )

    try:
        created = client.create_incident(
            payload.short_description,
            description=payload.description or None,
            caller_id=os.environ.get("SERVICENOW_DEFAULT_CALLER_SYS_ID") or None,
            category=payload.category,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not create incident in ServiceNow: {exc}",
        ) from exc

    return {"status": "created", "sys_id": created["sys_id"], "number": created["number"]}


@router.get("/incidents/{sys_id}/events")
def list_incident_events(sys_id: str):
    """Webhook events received for one incident, i.e. what the Business Rule sent."""
    db = SessionLocal()
    try:
        events = (
            db.execute(
                select(Event)
                .where(Event.incident_sys_id == sys_id)
                .order_by(Event.received_at)
            )
            .scalars()
            .all()
        )
        return {
            "sys_id": sys_id,
            "events": [
                {
                    "event_id": e.event_identifier,
                    "event_type": e.event_type,
                    "received_at": e.received_at.isoformat() if e.received_at else None,
                }
                for e in events
            ],
        }
    finally:
        db.close()


@router.delete("/incidents/{sys_id}")
async def delete_incident_via_dashboard(sys_id: str):
    from src.servicenow.client import ServiceNowClient

    try:
        ServiceNowClient().delete_incident(sys_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not delete incident in ServiceNow: {exc}") from exc

    db = SessionLocal()
    try:
        db.query(Event).filter(Event.incident_sys_id == sys_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    return {"status": "deleted", "sys_id": sys_id}


@router.post("/kb-sync")
async def trigger_kb_sync():
    try:
        from src.retrieval.ingest import sync_kb

        result = sync_kb()
        return {"status": "completed", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KB sync failed: {exc}") from exc

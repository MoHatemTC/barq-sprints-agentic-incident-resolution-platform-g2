"""Dashboard-only endpoints: real data reads + convenience actions.

These are additive and do not touch the existing (stubbed) executions.py
router used by the S2.2 contract tests. This router talks to the real
SQLAlchemy sync session (src.db.database.SessionLocal) so it can show
actual rows while S2.2's async wiring lands.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
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


class NewIncidentRequest(BaseModel):
    short_description: str = "Manual test incident from dashboard"
    sys_id: str | None = None
    number: str | None = None


@router.post("/incidents")
async def create_incident_via_dashboard(payload: NewIncidentRequest):
    import redis

    from dotenv import load_dotenv
    import os

    load_dotenv()

    if payload.sys_id:
        sys_id = payload.sys_id
        number = payload.number or f"INC{str(int(time.time()))[-7:]}"
    else:
        from src.servicenow.client import ServiceNowClient

        try:
            created = ServiceNowClient().create_incident(
                payload.short_description,
                caller_id=os.environ.get("SERVICENOW_DEFAULT_CALLER_SYS_ID") or None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not create incident in ServiceNow: {exc}",
            ) from exc
        sys_id, number = created["sys_id"], created["number"]
    event_id = f"evt_dashboard_{uuid.uuid4().hex[:12]}"

    db = SessionLocal()
    try:
        event = Event(
            event_identifier=event_id,
            incident_sys_id=sys_id,
            incident_number=number,
            event_type="incident.created",
            contract_version="v1",
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Could not create event: {exc}") from exc
    finally:
        db.close()

    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    r = redis.Redis(host=redis_host, port=redis_port, db=0)
    message = json.dumps(
        {
            "event_id": event_id,
            "sys_id": sys_id,
            "number": number,
            "event_type": "incident.created",
            "contract_version": "v1",
        }
    )
    r.rpush("incident_events", message)

    return {"status": "queued", "event_id": event_id, "sys_id": sys_id, "number": number}


@router.post("/kb-sync")
async def trigger_kb_sync():
    try:
        from src.retrieval.ingest import sync_kb

        result = sync_kb()
        return {"status": "completed", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KB sync failed: {exc}") from exc

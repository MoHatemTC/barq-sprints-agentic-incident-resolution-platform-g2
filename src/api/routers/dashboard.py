"""Dashboard-only endpoints: real data reads + convenience actions.

These are additive and do not touch the existing (stubbed) executions.py
router used by the S2.2 contract tests. This router talks to the real
SQLAlchemy sync session (src.db.database.SessionLocal) so it can show
actual rows while S2.2's async wiring lands.
"""

from __future__ import annotations

import json
import os
import threading
import time

from fastapi import APIRouter, HTTPException, Query
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


# --- Incident list: ServiceNow is the source of truth -------------------------
#
# The list is read live from ServiceNow (nothing is copied into Postgres), so it
# cannot drift from the instance. To keep the AI pipeline unaffected:
#   - one shared client, so the OAuth token is reused instead of fetched per poll;
#   - a short server-side cache shared by every open tab, so N tabs polling every
#     few seconds still cost at most one ServiceNow call per page per TTL;
#   - if ServiceNow is slow or down, the last page is served (marked stale)
#     instead of queueing requests behind it;
#   - AI status comes from Postgres with a fixed 4 indexed queries per page.

INCIDENT_CACHE_SECONDS = float(os.getenv("DASHBOARD_INCIDENT_CACHE_SECONDS", "10"))
MAX_INCIDENT_PAGE = 500  # enough for the whole demo instance history (284 today)
_MAX_CACHED_PAGES = 32


class _PageCache:
    """TTL cache with one refresh at a time per key; others get the stale copy."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict = {}      # key -> (expires_at, value)
        self._refreshing: dict = {}   # key -> Lock

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(self, key, fetch):
        """Return (value, stale_error). stale_error is set when an old copy was served."""
        with self._lock:
            entry = self._entries.get(key)
            if entry and time.monotonic() < entry[0]:
                return entry[1], None
            refresh = self._refreshing.setdefault(key, threading.Lock())

        # Someone else is already refreshing this page: don't wait behind them.
        if not refresh.acquire(blocking=entry is None):
            return entry[1], "refresh in progress"
        try:
            with self._lock:
                fresh = self._entries.get(key)
                if fresh and time.monotonic() < fresh[0]:
                    return fresh[1], None
            try:
                value = fetch()
            except Exception as exc:
                if entry:
                    return entry[1], str(exc)
                raise
            with self._lock:
                if len(self._entries) >= _MAX_CACHED_PAGES:
                    self._entries.clear()
                self._entries[key] = (time.monotonic() + self.ttl, value)
            return value, None
        finally:
            refresh.release()


_incident_pages = _PageCache(INCIDENT_CACHE_SECONDS)
_client_lock = threading.Lock()
_shared_client = None


def _servicenow():
    """One client for the dashboard list, so its OAuth token is reused."""
    global _shared_client
    with _client_lock:
        if _shared_client is None:
            from src.servicenow.client import ServiceNowClient

            _shared_client = ServiceNowClient()
        return _shared_client


def _incident_fields() -> dict:
    from src.servicenow import config

    ai = config.AI_FIELDS
    return {
        "sys_id": "sys_id",
        "number": "number",
        "short_description": "short_description",
        "category": "category",
        "state": "state",
        "priority": "priority",
        "created_at": "sys_created_on",
        "ai_processing_state": ai["processing_state"],
        "human_lock": ai["human_lock"],
        "ai_enabled": ai["ai_enabled"],
    }


def _field(row: dict, name: str, display: bool = False):
    cell = row.get(name)
    if isinstance(cell, dict):
        cell = cell.get("display_value" if display else "value")
    return cell if cell not in ("", None) else None


def _incident_from_servicenow(row: dict, fields: dict) -> dict:
    created = _field(row, fields["created_at"])  # UTC "YYYY-MM-DD HH:MM:SS"
    return {
        "sys_id": _field(row, fields["sys_id"]),
        "number": _field(row, fields["number"]),
        "short_description": _field(row, fields["short_description"]),
        "category": _field(row, fields["category"], display=True),
        "state": _field(row, fields["state"], display=True),
        "priority": _field(row, fields["priority"], display=True),
        "created_at": f"{created.replace(' ', 'T')}+00:00" if created else None,
        "ai_processing_state": _field(row, fields["ai_processing_state"], display=True),
        "human_lock": str(_field(row, fields["human_lock"])).lower() == "true",
        "ai_enabled": str(_field(row, fields["ai_enabled"])).lower() != "false",
    }


def _fetch_incident_page(limit: int, offset: int) -> dict:
    fields = _incident_fields()
    rows, total = _servicenow().list_incidents(list(fields.values()), limit=limit, offset=offset)
    return {"incidents": [_incident_from_servicenow(r, fields) for r in rows], "total": total}


def _latest_runs(db, numbers: list[str]) -> dict:
    """Latest execution per incident number, same shape as /executions minus the checkpoint list."""
    if not numbers:
        return {}
    runs = (
        db.execute(
            select(Execution)
            .where(Execution.incident_reference.in_(numbers))
            .order_by(Execution.incident_reference, desc(Execution.started_at))
            .distinct(Execution.incident_reference)
        )
        .scalars()
        .all()
    )
    ids = [run.execution_identifier for run in runs]
    if not ids:
        return {}

    failures: dict = {}
    for f in db.execute(
        select(Failure).where(Failure.execution_reference.in_(ids)).order_by(desc(Failure.id))
    ).scalars():
        failures.setdefault(f.execution_reference, []).append(
            {
                "failing_node": f.failing_node,
                "error_class": f.error_class,
                "message": f.message,
                "retry_count": f.retry_count,
            }
        )
    retries = {
        r.execution_reference: r.attempt_count
        for r in db.execute(select(RetryState).where(RetryState.execution_reference.in_(ids))).scalars()
    }
    latest_checkpoint = {
        cp.execution_reference: cp.checkpoint
        for cp in db.execute(
            select(WorkflowState)
            .where(WorkflowState.execution_reference.in_(ids))
            .order_by(
                WorkflowState.execution_reference,
                desc(WorkflowState.created_at),
                desc(WorkflowState.id),
            )
            .distinct(WorkflowState.execution_reference)
        ).scalars()
    }

    summaries = {}
    for run in runs:
        eid = run.execution_identifier
        raw = latest_checkpoint.get(eid)
        try:
            result = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            result = {"raw": raw}
        summaries[run.incident_reference] = {
            "execution_id": eid,
            "status": run.status,
            "node_reached": run.node_reached,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "duration_seconds": (
                (run.ended_at - run.started_at).total_seconds()
                if run.ended_at and run.started_at
                else None
            ),
            "latest_result": result,
            "failures": failures.get(eid, []),
            "retry_attempt_count": retries.get(eid, 0),
        }
    return summaries


@router.get("/incidents")
def list_incidents(limit: int = Query(20, ge=1, le=MAX_INCIDENT_PAGE), offset: int = Query(0, ge=0)):
    """ServiceNow incidents (newest first) with the latest AI run for each, if any.

    Plain def on purpose: FastAPI runs it in a worker thread, so a slow
    ServiceNow call never blocks the event loop that serves the webhook.
    """
    try:
        page, stale_error = _incident_pages.get((limit, offset), lambda: _fetch_incident_page(limit, offset))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not read incidents from ServiceNow: {exc}"
        ) from exc

    numbers = [i["number"] for i in page["incidents"] if i["number"]]
    db = SessionLocal()
    try:
        runs = _latest_runs(db, numbers)
    finally:
        db.close()

    return {
        "incidents": [{**i, "execution": runs.get(i["number"])} for i in page["incidents"]],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
        "stale": stale_error is not None,
        "stale_reason": stale_error,
    }


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

    _incident_pages.clear()  # show it on the next poll, not after the cache TTL
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
    _incident_pages.clear()

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

"""Dashboard-only endpoints: real data reads + convenience actions.

These are additive and do not touch the existing (stubbed) executions.py
router used by the S2.2 contract tests. This router talks to the real
SQLAlchemy sync session (src.db.database.SessionLocal) so it can show
actual rows while S2.2's async wiring lands.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, desc

from src.db.database import SessionLocal
from src.db.models import Event, Execution, Failure, RetryState, WorkflowState

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)


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
#   - if ServiceNow is slow or down, the last page is served (marked delayed)
#     instead of queueing requests behind it;
#   - AI status comes from Postgres with a fixed 4 indexed queries per page.
#
# Freshness: every page carries synced_at, the moment it was read from
# ServiceNow. Up to the cache TTL it is live; past STALE_AFTER_SECONDS it is
# stale and the dashboard shows a warning. The last good page is never dropped.

INCIDENT_CACHE_SECONDS = float(os.getenv("DASHBOARD_INCIDENT_CACHE_SECONDS", "10"))
STALE_AFTER_SECONDS = float(os.getenv("DASHBOARD_STALE_AFTER_SECONDS", "30"))
MAX_INCIDENT_PAGE = 500  # enough for the whole demo instance history (284 today)
_MAX_CACHED_PAGES = 32


def _sync_error_text(exc: Exception) -> str:
    """One short line for the dashboard; the full error goes to the API log."""
    from src.servicenow import exceptions as sn

    if isinstance(exc, sn.ServiceNowNetworkError):
        text = exc.message.lower()
        cause = (
            "timed out" if "timed out" in text or "timeout" in text
            else "connection refused" if "refused" in text
            else "address not found" if "resolve" in text or "name or service" in text
            else "network error"
        )
        return f"ServiceNow could not be reached ({cause})"
    if isinstance(exc, sn.ServiceNowAuthError):
        return "ServiceNow rejected the integration login (401)"
    if isinstance(exc, sn.ServiceNowPermissionError):
        return "ServiceNow denied access to incidents (403)"
    if isinstance(exc, sn.ServiceNowError):
        return f"ServiceNow returned an error ({exc.status_code})"
    return str(exc)[:200] or type(exc).__name__


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
                logger.warning("Dashboard refresh from ServiceNow failed: %r", exc)
                if entry:
                    return entry[1], _sync_error_text(exc)
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


def _servicenow_link(sys_id: str | None) -> str | None:
    """The incident form inside the full ServiceNow UI (navigation and all)."""
    if not sys_id:
        return None
    from urllib.parse import quote

    from src.servicenow import config

    return f"{config.INSTANCE_URL}/nav_to.do?uri={quote(f'incident.do?sys_id={sys_id}', safe='')}"


def _incident_from_servicenow(row: dict, fields: dict) -> dict:
    created = _field(row, fields["created_at"])  # UTC "YYYY-MM-DD HH:MM:SS"
    sys_id = _field(row, fields["sys_id"])
    return {
        "sys_id": sys_id,
        "servicenow_url": _servicenow_link(sys_id),
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


SEARCH_FIELDS = ("number", "short_description", "description")
MAX_SEARCH_WORDS = 5


def _search_query(q: str | None) -> str | None:
    """ServiceNow encoded query for the search box: every word must appear in the
    number, short description or description (contains, case-insensitive).

    '^' joins clauses in an encoded query, so it is removed: typed text can only
    ever be a search term, never an extra filter.
    """
    words = (q or "").replace("^", " ").split()[:MAX_SEARCH_WORDS]
    # a^ORb^ORc^d^ORe^ORf  ==  (a OR b OR c) AND (d OR e OR f)
    return "^".join(
        "^OR".join(f"{field}LIKE{word}" for field in SEARCH_FIELDS) for word in words
    ) or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_incident_page(limit: int, offset: int, query: str | None = None) -> dict:
    fields = _incident_fields()
    rows, total = _servicenow().list_incidents(
        list(fields.values()), limit=limit, offset=offset, query=query
    )
    return {
        "incidents": [_incident_from_servicenow(r, fields) for r in rows],
        "total": total,
        "synced_at": _utcnow(),
    }


def _live_graph():
    """The API's shared graph over the LangGraph checkpointer, or None if it is unavailable."""
    try:
        from src.api.routers.approvals import _compiled_graph

        return _compiled_graph()
    except Exception:
        return None


def _live_progress(graph, execution_id: str):
    """(state, running node) of an in-progress run from LangGraph's per-node checkpoint.

    The worker saves a dashboard checkpoint only when the run ends, but LangGraph
    persists state after every node, so this is read-only and costs the worker nothing.
    """
    if graph is None:
        return None, None
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": execution_id}})
    except Exception:
        return None, None
    if not snapshot or not snapshot.values:
        return None, None
    values = {k: v for k, v in snapshot.values.items() if k != "incident_payload"}
    state = json.loads(json.dumps(values, default=str))
    return state, (snapshot.next[0] if snapshot.next else None)


def _latest_runs(db, numbers: list[str], live_graph=None) -> dict:
    """Latest execution per incident number, same shape as /executions minus the checkpoint list.

    In-progress runs carry their live state and ``live_node`` (the node running now).
    ``live_graph`` returns the graph to read that from; it is only called when
    some run is in progress, so an idle list never touches the checkpoint store.
    """
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

    graph = None
    if live_graph is not None and any(run.status == "started" for run in runs):
        graph = live_graph()

    summaries = {}
    for run in runs:
        eid = run.execution_identifier
        raw = latest_checkpoint.get(eid)
        try:
            result = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            result = {"raw": raw}
        live_node = None
        if run.status == "started":
            live_state, live_node = _live_progress(graph, eid)
            result = live_state or result
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
            "live_node": live_node,
            "failures": failures.get(eid, []),
            "retry_attempt_count": retries.get(eid, 0),
        }
    return summaries


@router.get("/incidents")
def list_incidents(
    limit: int = Query(20, ge=1, le=MAX_INCIDENT_PAGE),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=100),
):
    """ServiceNow incidents (newest first) with the latest AI run for each, if any.

    ``q`` searches the incident number and the words of the descriptions.
    ``delayed``: the last refresh failed (``sync_error`` says why), so an older
    copy is shown. ``stale``: that copy is older than STALE_AFTER_SECONDS.
    Plain def on purpose: FastAPI runs it in a worker thread, so a slow
    ServiceNow call never blocks the event loop that serves the webhook.
    """
    query = _search_query(q)
    try:
        page, sync_error = _incident_pages.get(
            (limit, offset, query), lambda: _fetch_incident_page(limit, offset, query)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_sync_error_text(exc)) from exc

    numbers = [i["number"] for i in page["incidents"] if i["number"]]
    db = SessionLocal()
    try:
        runs = _latest_runs(db, numbers, live_graph=_live_graph)
    finally:
        db.close()

    age = max(0.0, (_utcnow() - page["synced_at"]).total_seconds())
    return {
        "incidents": [{**i, "execution": runs.get(i["number"])} for i in page["incidents"]],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
        "q": q if query else None,
        "synced_at": page["synced_at"].isoformat(),
        "age_seconds": round(age, 1),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "stale": age > STALE_AFTER_SECONDS,
        "delayed": sync_error is not None,
        "sync_error": sync_error,
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
        raise HTTPException(status_code=502, detail=_sync_error_text(exc)) from exc


class NewIncidentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    short_description: str = Field(..., min_length=1)
    description: str | None = None
    category: str = Field(..., min_length=1)
    # One id per New incident form, reused on every retry of that form.
    request_id: str | None = Field(None, pattern=r"^[A-Za-z0-9-]{8,64}$")


@router.post("/incidents", status_code=201)
def create_incident_via_dashboard(payload: NewIncidentRequest, response: Response):
    """Create an incident in ServiceNow, the same as filling in the ServiceNow form.

    Nothing is stored or queued here. The insert fires the AI Eligibility Check
    Business Rule (servicenow/ai_incident_orchestrator/update_set.xml),
    which alone decides whether the incident reaches the webhook.

    No duplicates on retry: ``request_id`` is saved in the incident's
    correlation_id. If ServiceNow created the incident but the answer never
    arrived (timeout), the retry finds it and returns it (200) instead of
    creating a second one.
    """
    import os

    from src.servicenow.client import ServiceNowClient

    client = ServiceNowClient()
    try:
        categories = {c["value"] for c in _category_choices(client)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_sync_error_text(exc)) from exc
    # The Table API stores any string in a choice field; the form does not.
    if payload.category not in categories:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.category}' is not an incident category in ServiceNow",
        )

    correlation_id = f"barq-dashboard-{payload.request_id}" if payload.request_id else None
    if correlation_id:
        try:
            existing = client.find_incident_by_correlation(correlation_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_sync_error_text(exc)) from exc
        if existing:
            response.status_code = 200
            _incident_pages.clear()
            return {"status": "already_created", "sys_id": existing["sys_id"], "number": existing["number"]}

    try:
        created = client.create_incident(
            payload.short_description,
            description=payload.description or None,
            caller_id=os.environ.get("SERVICENOW_DEFAULT_CALLER_SYS_ID") or None,
            category=payload.category,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_sync_error_text(exc)) from exc

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

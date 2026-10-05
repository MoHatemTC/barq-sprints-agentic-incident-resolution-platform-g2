"""Catch-up sweep for incidents whose webhook never arrived.

The AI Eligibility Check Business Rule (servicenow/ai_incident_orchestrator/
update_set.xml) posts each eligible incident to the webhook once, with
``executeAsync()``: ServiceNow never reads the reply and never retries. When the
backend is unreachable (ngrok down, API restarting) the incident just stays
Pending. The backend can still reach ServiceNow, so every minute this sweep asks
for eligible Pending incidents we hold no event for, and feeds them into the
same path the webhook uses: persist an Event, then RPUSH it.

Runs in its own thread in the consumer process, so a slow ServiceNow call never
holds up the Redis-to-Celery dispatch loop.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db.models import Event
from src.servicenow.config import AI_FIELDS
from src.workers.redis_consumer import INCIDENT_EVENTS_LIST

logger = logging.getLogger(__name__)

# Mirrors the AI Eligibility Check Business Rule. Keep the two in sync.
SUPPORTED_CATEGORIES = ("software", "hardware", "network", "database", "password_reset")

# Leave the Business Rule's own webhook time to land before stepping in.
# Set DELIVERY_SWEEP_GRACE_MINUTES=0 in .env to pick up incidents immediately
# (safe when ngrok is reliable; may double-dispatch if webhook also fires).
GRACE_MINUTES = int(os.getenv("DELIVERY_SWEEP_GRACE_MINUTES", "2"))
# Do not pull in old backlog the pipeline never saw.
LOOKBACK_MINUTES = 24 * 60
BATCH_LIMIT = 100

SWEEP_EVENT_TYPE = "Sweep"
DEFAULT_INTERVAL_SECONDS = 60


def eligible_pending_query() -> str:
    """Incidents the Business Rule would send, still Pending, untouched for GRACE_MINUTES.

    Filtering on sys_updated_on rather than sys_created_on also covers the
    rule's Update path (e.g. category changed to a supported one), and the
    grace window keeps the sweep from racing a webhook that is still in flight.
    """
    state, lock = AI_FIELDS["processing_state"], AI_FIELDS["human_lock"]
    return "^".join(
        [
            "active=true",
            f"{AI_FIELDS['ai_enabled']}=true",
            f"{lock}=false^OR{lock}ISEMPTY",
            "categoryIN" + ",".join(SUPPORTED_CATEGORIES),
            f"{state}=pending^OR{state}ISEMPTY",
            f"sys_updated_onRELATIVEGT@minute@ago@{LOOKBACK_MINUTES}",
            f"sys_updated_onRELATIVELT@minute@ago@{GRACE_MINUTES}",
        ]
    )


def sweep_once(registry, session_factory: Callable, redis_client) -> list[str]:
    """Queue every eligible Pending incident we have no event for. Returns their numbers."""
    from src.agent.tools.registry import ToolRefusal

    rows = registry.dispatch(
        "list_incidents",
        f"delivery-sweep-{uuid.uuid4().hex[:12]}",
        query=eligible_pending_query(),
        fields=["sys_id", "number"],
        limit=BATCH_LIMIT,
    )
    if isinstance(rows, ToolRefusal):
        logger.warning("Delivery sweep: registry refused list_incidents: %s", rows.message)
        return []

    candidates = _candidates(rows or [])
    if not candidates:
        return []

    queued = []
    db = session_factory()
    try:
        seen = set(
            db.execute(
                select(Event.incident_sys_id).where(Event.incident_sys_id.in_(candidates))
            ).scalars()
        )
        for sys_id, number in candidates.items():
            if sys_id in seen:
                continue
            # One fixed id per incident: a repeat sweep hits the unique
            # constraint instead of queueing the incident twice.
            event_id = f"sweep-{sys_id}"
            db.add(
                Event(
                    event_identifier=event_id,
                    incident_sys_id=sys_id,
                    incident_number=number,
                    event_type=SWEEP_EVENT_TYPE,
                    contract_version="v1",
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            # Same message shape the webhook enqueues (Payload.model_dump_json()).
            redis_client.rpush(
                INCIDENT_EVENTS_LIST,
                json.dumps(
                    {
                        "event_id": event_id,
                        "sys_id": sys_id,
                        "number": number,
                        "event_type": SWEEP_EVENT_TYPE,
                        "contract_version": "v1",
                    }
                ),
            )
            queued.append(number)
            logger.warning("Delivery sweep queued %s: its webhook never arrived", number)
    finally:
        db.close()
    return queued


def run_delivery_sweep(
    sweep: Callable[[], object],
    interval_seconds: float,
    should_stop: Callable[[], bool],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run ``sweep`` every ``interval_seconds``; one failed pass never stops the loop."""
    while not should_stop():
        try:
            sweep()
        except Exception:
            logger.exception("Delivery sweep iteration failed")
        sleep(interval_seconds)


def start_delivery_sweep(redis_client, interval_seconds: float | None = None):
    """Start the sweep in a daemon thread. DELIVERY_SWEEP_INTERVAL_SECONDS=0 disables it."""
    if interval_seconds is None:
        interval_seconds = float(
            os.getenv("DELIVERY_SWEEP_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
        )
    if interval_seconds <= 0:
        logger.warning("Delivery sweep disabled")
        return None

    from src.agent.tools.registry import DEFAULT_TOOL_REGISTRY
    from src.db.database import SessionLocal

    thread = threading.Thread(
        target=run_delivery_sweep,
        args=(
            lambda: sweep_once(DEFAULT_TOOL_REGISTRY, SessionLocal, redis_client),
            interval_seconds,
            lambda: False,
        ),
        name="delivery-sweep",
        daemon=True,
    )
    thread.start()
    return thread


def _candidates(rows: Iterable[Mapping]) -> dict[str, str]:
    # list_incidents asks for display_value=all, so each field is {"value": ...}.
    candidates = {}
    for row in rows:
        sys_id, number = _value(row.get("sys_id")), _value(row.get("number"))
        if sys_id and number:
            candidates[sys_id] = number
    return candidates


def _value(field) -> str:
    if isinstance(field, Mapping):
        field = field.get("value")
    return str(field or "").strip()

"""The delivery sweep picks up eligible incidents whose webhook never arrived.

Driven through a real ToolRegistry over a real IncidentGateway (only the
innermost ServiceNow client is fake) and the real events table.
"""

import json
import uuid

import pytest

from src.db.database import SessionLocal
from src.db.models import Event
from src.workers import delivery_sweep
from src.workers.delivery_sweep import (
    eligible_pending_query,
    run_delivery_sweep,
    start_delivery_sweep,
    sweep_once,
)
from tests.agent_registry_helpers import FakeServiceNowClient, build_registry


class FakeRedis:
    def __init__(self):
        self.pushed = []

    def rpush(self, key, message):
        self.pushed.append((key, json.loads(message)))


def _row(sys_id, number):
    # list_incidents asks for display_value=all
    return {"sys_id": {"value": sys_id, "display_value": sys_id},
            "number": {"value": number, "display_value": number}}


@pytest.fixture
def sys_ids():
    ids = [f"sweep-test-{uuid.uuid4().hex}" for _ in range(2)]
    yield ids
    db = SessionLocal()
    try:
        db.query(Event).filter(Event.incident_sys_id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _events_for(sys_id):
    db = SessionLocal()
    try:
        return db.query(Event).filter(Event.incident_sys_id == sys_id).all()
    finally:
        db.close()


def test_query_mirrors_the_business_rule_eligibility_checks():
    query = eligible_pending_query()

    assert "active=true" in query
    assert "x_2215689_ai_inc_0_ai_enabled=true" in query
    assert "x_2215689_ai_inc_0_human_lock=false^ORx_2215689_ai_inc_0_human_lockISEMPTY" in query
    assert "categoryINsoftware,hardware,network,database,password_reset" in query
    assert ("x_2215689_ai_inc_0_u_ai_processing_state=pending"
            "^ORx_2215689_ai_inc_0_u_ai_processing_stateISEMPTY") in query
    # grace window so the Business Rule's own webhook lands first, and no old backlog
    assert f"sys_updated_onRELATIVELT@minute@ago@{delivery_sweep.GRACE_MINUTES}" in query
    assert "sys_updated_onRELATIVEGT@minute@ago@1440" in query


def test_missed_incident_is_persisted_and_queued_like_the_webhook(sys_ids):
    client = FakeServiceNowClient(list_incidents_result=[_row(sys_ids[0], "INC0099001")])
    redis = FakeRedis()

    queued = sweep_once(build_registry(client), SessionLocal, redis)

    assert queued == ["INC0099001"]
    assert client.list_queries == [eligible_pending_query()]
    [event] = _events_for(sys_ids[0])
    assert (event.event_identifier, event.event_type, event.contract_version) == (
        f"sweep-{sys_ids[0]}", "Sweep", "v1",
    )
    assert redis.pushed == [("incident_events", {
        "event_id": f"sweep-{sys_ids[0]}", "sys_id": sys_ids[0], "number": "INC0099001",
        "event_type": "Sweep", "contract_version": "v1",
    })]


def test_incident_whose_webhook_arrived_is_left_alone(sys_ids):
    db = SessionLocal()
    db.add(Event(event_identifier=f"evt-{sys_ids[0]}", incident_sys_id=sys_ids[0],
                 incident_number="INC0099001", event_type="Insert", contract_version="v1"))
    db.commit()
    db.close()
    client = FakeServiceNowClient(list_incidents_result=[
        _row(sys_ids[0], "INC0099001"), _row(sys_ids[1], "INC0099002"),
    ])
    redis = FakeRedis()

    queued = sweep_once(build_registry(client), SessionLocal, redis)

    assert queued == ["INC0099002"]
    assert [e.event_identifier for e in _events_for(sys_ids[0])] == [f"evt-{sys_ids[0]}"]


def test_a_second_sweep_does_not_queue_the_same_incident_again(sys_ids):
    client = FakeServiceNowClient(list_incidents_result=[_row(sys_ids[0], "INC0099001")])
    redis = FakeRedis()
    registry = build_registry(client)

    sweep_once(registry, SessionLocal, redis)
    assert sweep_once(registry, SessionLocal, redis) == []

    assert len(redis.pushed) == 1
    assert len(_events_for(sys_ids[0])) == 1


def test_refused_read_queues_nothing(sys_ids):
    client = FakeServiceNowClient(list_incidents_result=[_row(sys_ids[0], "INC0099001")])
    redis = FakeRedis()

    queued = sweep_once(build_registry(client, tools={"list_incidents": "absent"}), SessionLocal, redis)

    assert queued == []
    assert redis.pushed == []
    assert "list_incidents" not in client.calls


def test_a_failed_pass_does_not_stop_the_loop():
    passes, sleeps = [], []

    def sweep():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("ServiceNow timed out")

    run_delivery_sweep(sweep, 60, should_stop=lambda: len(passes) >= 3, sleep=sleeps.append)

    assert len(passes) == 3
    assert sleeps == [60, 60, 60]


def test_interval_zero_disables_the_sweep(monkeypatch):
    monkeypatch.setattr(delivery_sweep.threading, "Thread", lambda **kw: pytest.fail("must not start"))
    assert start_delivery_sweep(FakeRedis(), interval_seconds=0) is None

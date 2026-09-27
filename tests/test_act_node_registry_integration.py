"""End-to-end proof that act_node's *write path* goes through the registry.

Every other registry test dispatches in isolation: it builds a registry, calls
dispatch() or a handler, and asserts on the return. None of them touch act_node.
That is precisely why the reported gap survived a green suite -- the registry
was correct, fully tested, and completely bypassed by the code that actually
writes to ServiceNow.

These tests close that gap at the level it appeared. They drive act_node with a
real AgentState and assert on the ServiceNow writes that reach the client:

  * the happy path only writes because dispatch() ran, and a registry missing a
    tool act_node needs halts the node instead of writing;
  * a refusal is a terminal failure, never reported as servicenow_write="written";
  * an unapproved HIGH_RISK tool is refused, so the approval gate governs the
    real path and not just a unit test.

The registry and IncidentGateway are real; only the innermost client is faked.
"""

import pytest

from src.agent.nodes import act as act_module
from src.agent.nodes.act import ExecutionLogNotWritten, ToolRefused, act_node
from tests.agent_registry_helpers import (
    FakeServiceNowClient,
    build_registry,
    install_registry,
)

pytestmark = pytest.mark.usefixtures("consumed_aware_trigger")

SYS_ID = "sys-0001"
EXEC_ID = "act-refusal-1"


def _state(**overrides):
    """A minimal state that reaches the write path (sys_id + execution_id)."""
    state = {
        "execution_id": EXEC_ID,
        "incident_number": "INC0001",
        "incident_payload": {"sys_id": SYS_ID},
        "outputs": {"resolution": "Reset the VPN credentials"},
        "confidence": 0.91,
        "classification": "access",
    }
    state.update(overrides)
    return state


def _install(monkeypatch, **kwargs):
    client = FakeServiceNowClient()
    install_registry(monkeypatch, "src.agent.nodes.act", build_registry(client, **kwargs))
    return client


# ── the happy path, proving the write really is a dispatch ──────────────────

def test_act_node_writes_through_the_registry(monkeypatch):
    client = _install(monkeypatch)

    result = act_node(_state())

    assert result["servicenow_write"] == "written"
    assert result["action_taken"] == "resolved_automatically"
    # The write reached the client, and only via the gateway.
    assert client.calls == ["find_execution_log", "update_incident", "write_execution_log"]
    assert client.incident_patches[0]["processing_state"] == "complete"
    assert client.incident_patches[0]["resolution"] == "Reset the VPN credentials"
    assert len(client.logs) == 1
    assert client.logs[0]["execution_id"] == EXEC_ID


def test_act_node_second_run_is_idempotent(monkeypatch):
    """S3.4's exactly-once guarantee must survive the rewiring.

    The idempotency probe is itself a registry dispatch now, so this covers the
    case where routing it through a permission check could have broken it.
    """
    client = _install(monkeypatch)

    assert act_node(_state())["servicenow_write"] == "written"
    assert act_node(_state())["servicenow_write"] == "already_done"
    assert len(client.logs) == 1
    assert client.calls.count("update_incident") == 1


# ── the gap this file exists for ───────────────────────────────────────────

def test_act_node_halts_when_write_tool_is_unregistered(monkeypatch):
    """An unregistered write tool must stop the node, not fall back to a write.

    This is the regression the enforcement test could not see: with
    write_ai_fields absent, a node that reached for a client directly would
    happily write anyway. Here it must raise before any write reaches the client.
    """
    client = _install(monkeypatch, tools={"write_ai_fields": "absent"})

    with pytest.raises(ToolRefused) as excinfo:
        act_node(_state())

    refusal = excinfo.value.refusal
    assert refusal.tool == "write_ai_fields"
    assert refusal.reason == "unregistered_tool"
    assert refusal.execution_id == EXEC_ID
    # Nothing was written: no incident patch, and no receipt claiming one.
    assert client.incident_patches == []
    assert client.logs == []


def test_act_node_halts_when_receipt_tool_is_unregistered(monkeypatch):
    """A refusal on the receipt halts too, rather than reporting a bare write."""
    client = _install(monkeypatch, tools={"write_execution_log": "absent"})

    with pytest.raises(ToolRefused) as excinfo:
        act_node(_state())

    assert excinfo.value.refusal.tool == "write_execution_log"
    assert excinfo.value.refusal.reason == "unregistered_tool"
    assert client.logs == []


def test_act_node_halts_when_idempotency_probe_is_unregistered(monkeypatch):
    """Without the READ probe the node cannot prove it has not written before.

    It must halt rather than assume, because assuming would risk the duplicate
    write the probe exists to prevent.
    """
    client = _install(monkeypatch, tools={"find_execution_log": "absent"})

    with pytest.raises(ToolRefused) as excinfo:
        act_node(_state())

    assert excinfo.value.refusal.tool == "find_execution_log"
    assert client.incident_patches == []
    assert client.logs == []


def test_refusal_is_terminal_not_retryable(monkeypatch):
    """A refusal must dead-letter, not burn retries.

    Retrying cannot make an unregistered tool registered or an unconsumed
    approval appear, so ToolRefused is terminal. If this ever became retryable
    the worker would re-run act against the same refusal until the retry budget
    ran out, and still have written nothing.
    """
    assert ToolRefused.retryable is False
    assert ExecutionLogNotWritten.retryable is True

    from src.workers.retry_policy import RetryPolicy

    policy = RetryPolicy(max_retries=3, base_delay_seconds=1, max_delay_seconds=8)
    assert policy.decide(ToolRefused.__new__(ToolRefused), 0).name == "TERMINAL"
    assert policy.decide(ExecutionLogNotWritten("x"), 0).name == "RETRY"


def test_act_node_refusal_is_never_reported_as_a_successful_write(monkeypatch):
    """No state may claim a write happened when the registry refused.

    dispatch() signals refusal by *returning* a ToolRefusal. A caller that
    forgot to check would fall through and return servicenow_write="written"
    having written nothing, so assert on the state, not just the raise.
    """
    client = _install(monkeypatch, tools={"write_ai_fields": "absent"})

    with pytest.raises(ToolRefused) as excinfo:
        act_node(_state())

    # Raising means act_node produced no state at all, so no downstream node or
    # worker caller can read a servicenow_write="written" that never happened.
    # The fake client saw only the idempotency probe, never a write.
    assert client.calls == ["find_execution_log"]
    assert client.incident_patches == []
    assert excinfo.value.refusal.reason == "unregistered_tool"


def test_unapproved_high_risk_tool_is_refused_on_the_real_path(monkeypatch):
    """The approval gate must govern act_node, not only a unit test.

    write_ai_fields is re-registered here as HIGH_RISK. The registry is built
    fresh so the name is free, and with no approval record for this execution
    the dispatch must be refused before the handler runs.
    """
    from src.agent.tools.permissions import PermissionClass
    from src.agent.tools.registry import ToolRegistry
    from src.servicenow.client import IncidentGateway

    client = FakeServiceNowClient()
    gateway = IncidentGateway(client=client)
    registry = ToolRegistry()
    registry.register("find_execution_log", PermissionClass.READ, gateway.find_execution_log)
    registry.register("write_ai_fields", PermissionClass.HIGH_RISK, gateway.write_ai_fields)
    registry.register(
        "write_execution_log", PermissionClass.LOW_RISK_WRITE, gateway.write_execution_log
    )
    install_registry(monkeypatch, "src.agent.nodes.act", registry)

    with pytest.raises(ToolRefused) as excinfo:
        act_node(_state(execution_id="act-unapproved-1"))

    assert excinfo.value.refusal.tool == "write_ai_fields"
    assert excinfo.value.refusal.reason == "approval_required"
    assert client.incident_patches == []


def test_act_node_never_touches_a_client_outside_the_gateway(monkeypatch):
    """Guard the rewiring itself: no ServiceNowClient construction in act.py.

    Complements the static boundary test by asserting the *seam* act_node uses
    is the registry, so a future edit cannot quietly reintroduce a client.
    """
    _install(monkeypatch)
    act_node(_state())

    assert act_module.TOOL_REGISTRY is not None
    assert not hasattr(act_module, "get_servicenow_client"), (
        "act_node must not expose a raw-client factory; that seam is how the "
        "node bypassed the registry in the first place"
    )

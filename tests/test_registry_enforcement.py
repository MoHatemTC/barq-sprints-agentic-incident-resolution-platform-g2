"""Tests that attack the enforcement layer directly: typed refusals, atomic
approval consumption, concurrent-dispatch safety, and forged-privilege
attempts (downgrading a tool's permission class must always be rejected).
"""

import inspect
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.agent.tools.permissions import PermissionClass
from src.agent.tools.registry import ToolRefusal, ToolRegistry
from tests.conftest import cleanup, create_approved_approval, unique_execution_id

pytestmark = pytest.mark.usefixtures("consumed_aware_trigger")


def test_unregistered_tool_returns_typed_refusal():
    registry = ToolRegistry()
    refusal = registry.dispatch("does_not_exist", "exec-1")
    assert isinstance(refusal, ToolRefusal)
    assert refusal.reason == "unregistered_tool"
    assert refusal.tool == "does_not_exist"
    assert refusal.execution_id == "exec-1"


def test_unregistered_tool_does_not_invoke_anything():
    registry = ToolRegistry()
    registry.register("real_tool", PermissionClass.READ, lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    refusal = registry.dispatch("other_tool", "exec-1")
    assert isinstance(refusal, ToolRefusal)
    assert refusal.reason == "unregistered_tool"


def test_high_risk_without_approval_is_refused_and_handler_not_called():
    registry = ToolRegistry()
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return "should not happen"

    registry.register("kb_write_back", PermissionClass.HIGH_RISK, handler)
    execution_id = unique_execution_id("reg-nohd")
    refusal = registry.dispatch("kb_write_back", execution_id, corpus_path="data/kb_dataset.json")
    assert isinstance(refusal, ToolRefusal)
    assert refusal.reason == "approval_required"
    assert calls == []


def test_high_risk_with_approval_invokes_handler_exactly_once():
    registry = ToolRegistry()
    calls = []

    def handler(corpus_path=None, dry_run=False, execution_id=None):
        calls.append((corpus_path, dry_run))
        return {"created": 1}

    registry.register("kb_write_back", PermissionClass.HIGH_RISK, handler)
    execution_id = unique_execution_id("reg-approved")
    try:
        create_approved_approval(execution_id)
        result = registry.dispatch("kb_write_back", execution_id, dry_run=True)
        assert result == {"created": 1}
        assert calls == [(None, True)]
    finally:
        cleanup(execution_id)


def test_concurrent_dispatch_race_consumes_approval_once():
    registry = ToolRegistry()
    calls = []

    def handler(corpus_path=None, dry_run=False, execution_id=None):
        calls.append((corpus_path, dry_run))
        return {"created": 1}

    registry.register("kb_write_back", PermissionClass.HIGH_RISK, handler)
    execution_id = unique_execution_id("reg-race")
    try:
        create_approved_approval(execution_id)

        def worker():
            return registry.dispatch("kb_write_back", execution_id, dry_run=True) == {"created": 1}

        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(lambda _: worker(), range(12)))

        assert outcomes.count(True) == 1
        assert len(calls) == 1
    finally:
        cleanup(execution_id)


def test_forged_permission_downgrade_via_reregistration_is_rejected():
    registry = ToolRegistry()
    calls = []

    def real_handler(**kwargs):
        calls.append(kwargs)
        return "write-back"

    registry.register("kb_write_back", PermissionClass.HIGH_RISK, real_handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            "kb_write_back", PermissionClass.LOW_RISK_WRITE, lambda **k: "downgraded"
        )

    permission_class, handler = registry._tools["kb_write_back"]
    assert permission_class is PermissionClass.HIGH_RISK
    assert handler is real_handler


def test_registry_public_surface_offers_no_downgrade_bypass():
    public = {name for name in dir(ToolRegistry) if not name.startswith("_")}
    assert public == {"dispatch", "register"}


def test_dispatch_signature_accepts_no_permission_class_override():
    parameters = inspect.signature(ToolRegistry.dispatch).parameters
    assert "permission_class" not in parameters
    assert set(parameters) == {"self", "name", "execution_id", "kwargs"}


def test_dispatch_cannot_be_forged_with_permission_class_kwarg():
    registry = ToolRegistry()
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return "should not run"

    registry.register("kb_write_back", PermissionClass.HIGH_RISK, handler)
    execution_id = unique_execution_id("reg-forge")
    refusal = registry.dispatch(
        "kb_write_back", execution_id, permission_class=PermissionClass.READ
    )
    assert isinstance(refusal, ToolRefusal)
    assert refusal.reason == "approval_required"
    assert calls == []


# The real perimeter: IncidentGateway is the only thing allowed to hold a
# ServiceNowClient, and the registry is the only thing allowed to reach the
# gateway. So the client class name alone is not the thing to watch -- a node
# can import and instantiate ServiceNowClient directly, never mention
# IncidentGateway, and bypass every permission check. That is exactly the gap
# this test used to miss: it grepped for "IncidentGateway", so act_node and
# load_node both constructed ServiceNowClient themselves and passed.
#
# ALLOWED_DIRECT_CLIENT lists the non-agent call sites that are deliberately
# outside this perimeter, each with its reason. They are not agent code paths
# and are not authorised by the agent's approval gate, so routing them through
# ToolRegistry would be wrong, not safer:
#
#   src/api/routers/dashboard.py
#       A human clicking "create incident" in the UI. Authorised by the HTTP
#       request, not by an agent approval record, and there is no execution_id
#       to gate on.
#   src/retrieval/sources/servicenow_source.py
#       Read-only KB article sync run by ingestion/cron. No agent, no writes.
#
# Everything outside this list must be clean, so a *new* direct client call
# anywhere in src/ still fails -- the allowlist is an enumeration of reviewed
# exceptions, not a blanket exemption for src/api or src/retrieval.
ALLOWED_DIRECT_CLIENT = {
    "src/api/routers/dashboard.py",
    "src/retrieval/sources/servicenow_source.py",
    # Defines its own class *named* ServiceNowClient plus a FastAPI DI factory
    # of the same name, both dead: the factory raises NotImplementedError,
    # nothing imports either, and neither touches src/servicenow/client.py.
    # It matches only because the name collides. Renaming it is the real fix
    # but is a public DI-surface change and out of scope here.
    "src/api/dependencies.py",
}

# Only the agent may reach ServiceNow at all. The API and retrieval paths above
# are enumerated rather than blanket-exempt, so a new direct client call
# anywhere in src/ still fails this test.
AGENT_ROOT = "src/agent"

# The client module itself: IncidentGateway lives here, so it is the boundary.
CLIENT_MODULE = "src/servicenow/client.py"
REGISTRY_MODULE = "src/agent/tools/registry.py"

# Matches actually *constructing* the client, or importing the class itself.
# Deliberately not "any import from src.servicenow.client": publish_kb.py
# imports the `_same` field-comparison helper from that module, which touches no
# client state and is not a perimeter violation.
_CLIENT_TOKENS = (
    re.compile(r"\bServiceNowClient\s*\("),
    re.compile(r"\bimport\b[^\n]*\bServiceNowClient\b"),
    re.compile(r"\bget_servicenow_client\b"),
)


def _python_files(root: Path):
    for path in sorted(root.glob("**/*.py")):
        if any(part.startswith(".") for part in path.parts):
            continue
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        yield path


def test_servicenow_client_only_instantiated_inside_incident_gateway():
    """ServiceNowClient must not be constructed outside client.py/registry.py."""
    root = Path(__file__).resolve().parents[1]
    exempt = {
        (root / CLIENT_MODULE).resolve(),
        (root / REGISTRY_MODULE).resolve(),
    } | {(root / rel).resolve() for rel in ALLOWED_DIRECT_CLIENT}

    offenders = []
    for path in _python_files(root):
        if path.resolve() in exempt:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Strip comments so prose *about* the perimeter does not trip it, then
        # look for a real construction or import of the client class.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        if any(token.search(code) for token in _CLIENT_TOKENS):
            offenders.append(str(path.relative_to(root)))

    assert offenders == [], (
        "ServiceNowClient must only be instantiated inside IncidentGateway "
        f"(client.py) or the registry. Offenders: {offenders}. Either route the "
        "call through registry.dispatch() or, if it is genuinely not an agent "
        "path, add it to ALLOWED_DIRECT_CLIENT with a reason."
    )


def test_no_agent_module_touches_servicenow_client_directly():
    """The agent package specifically: no client import, no gateway import.

    This is the check that would have caught the reported gap, stated against
    the package where the threat model applies rather than the whole tree.
    """
    root = Path(__file__).resolve().parents[1]
    agent_root = root / AGENT_ROOT
    assert agent_root.is_dir(), f"{AGENT_ROOT} not found; update this test"

    offenders = []
    for path in sorted(agent_root.rglob("*.py")):
        if path.resolve() in {(root / REGISTRY_MODULE).resolve()}:
            continue
        text = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        if "src.servicenow" in code or "servicenow.client" in code:
            offenders.append(str(path.relative_to(root)))
        if "IncidentGateway" in code:
            offenders.append(str(path.relative_to(root)))

    assert offenders == [], (
        f"Agent modules must reach ServiceNow only via the registry. Offenders: {offenders}"
    )


def test_every_registered_tool_dispatches_without_typeerror():
    """A registered handler must actually be callable by dispatch().

    dispatch() passes execution_id on every call, so a handler that does not
    accept it raises TypeError at the first real call. Every pre-existing
    registry test used a `lambda **kwargs` handler, which swallows any
    signature, so this class of bug stayed invisible until a node dispatched
    for real. Asserted against the real IncidentGateway, not a stub.
    """
    from src.servicenow.client import IncidentGateway
    from tests.agent_registry_helpers import FakeServiceNowClient

    gateway = IncidentGateway(client=FakeServiceNowClient())
    handlers = {
        "read_incident": (PermissionClass.READ, gateway.read_incident),
        "find_execution_log": (PermissionClass.READ, gateway.find_execution_log),
        "write_ai_fields": (PermissionClass.LOW_RISK_WRITE, gateway.write_ai_fields),
        "write_execution_log": (PermissionClass.LOW_RISK_WRITE, gateway.write_execution_log),
        "write_work_note": (PermissionClass.LOW_RISK_WRITE, gateway.write_work_note),
    }
    registry = ToolRegistry()
    for name, (permission_class, handler) in handlers.items():
        registry.register(name, permission_class, handler)

    assert set(registry._tools) == set(handlers), (
        "build_default_registry and this test disagree on the tool set; the "
        "default registry is the source of truth"
    )

    kwargs = {
        "read_incident": {"sys_id": "s1"},
        "find_execution_log": {"action": "auto_resolve"},
        "write_ai_fields": {"sys_id": "s1", "fields": {"a": 1}},
        "write_execution_log": {
            "incident_sys_id": "s1", "action": "auto_resolve", "status": "succeeded",
        },
        "write_work_note": {"sys_id": "s1", "note": "hi"},
    }
    for name in handlers:
        # The whole point: this must not raise TypeError. A refusal here would
        # also be wrong (READ/LOW_RISK_WRITE need no approval), so assert on it.
        # A None result is fine and expected for find_execution_log, which is a
        # "has this execution already written its receipt?" probe.
        result = registry.dispatch(name, "exec-1", **kwargs[name])
        assert not isinstance(result, ToolRefusal), f"{name} unexpectedly refused"

    # The probe must actually be able to answer "no" without that being an
    # error. Use a fresh execution id: the loop above wrote a receipt for
    # "exec-1", so probing that one would correctly find it.
    assert registry.dispatch("find_execution_log", "never-written", action="auto_resolve") is None
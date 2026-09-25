"""Tests that attack the enforcement layer directly: typed refusals, atomic
approval consumption, concurrent-dispatch safety, and forged-privilege
attempts (downgrading a tool's permission class must always be rejected).
"""

import inspect
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


def test_import_boundary_incident_gateway_only_used_via_registry():
    root = Path(__file__).resolve().parents[1]
    forbidden = {root / "src/servicenow/client.py", root / "src/agent/tools/registry.py"}
    offenders = []
    for path in sorted(root.glob("**/*.py")):
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.resolve() in {p.resolve() for p in forbidden}:
            continue
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        try:
            text_content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "IncidentGateway" in text_content:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"IncidentGateway must only be invoked via the registry, found in: {offenders}"
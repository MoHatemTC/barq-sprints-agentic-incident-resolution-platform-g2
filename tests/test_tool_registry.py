"""Structural tests for the ToolRegistry: registration mechanics and the
default five-tool wiring. These tests never touch the approval table — the
READ / LOW_RISK_WRITE gate always passes without a database round-trip.
"""

import logging

import pytest

from src.agent.tools.permissions import PermissionClass
from src.agent.tools.registry import ToolRegistry, build_default_registry


def test_register_adds_tool():
    registry = ToolRegistry()
    handler = lambda **kwargs: "ok"
    registry.register("read_incident", PermissionClass.READ, handler)
    assert set(registry._tools) == {"read_incident"}
    assert registry._tools["read_incident"] == (PermissionClass.READ, handler)


def test_register_duplicate_name_raises():
    registry = ToolRegistry()
    registry.register("read_incident", PermissionClass.READ, lambda **k: "ok")
    with pytest.raises(ValueError, match="already registered"):
        registry.register("read_incident", PermissionClass.READ, lambda **k: "ok")


def test_register_non_callable_raises():
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="not callable"):
        registry.register("read_incident", PermissionClass.READ, "not-callable")


def test_register_wrong_permission_class_type_raises():
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="not a PermissionClass"):
        registry.register("read_incident", "READ", lambda **k: "ok")


def test_read_dispatch_invokes_handler_with_kwargs():
    registry = ToolRegistry()
    received = {}

    def handler(sys_id, **kwargs):
        received["sys_id"] = sys_id
        received["kwargs"] = kwargs
        return {"sys_id": sys_id}

    registry.register("read_incident", PermissionClass.READ, handler)
    result = registry.dispatch("read_incident", "exec-1", sys_id="sys-abc")
    assert result == {"sys_id": "sys-abc"}
    assert received["sys_id"] == "sys-abc"


def test_execution_id_is_injected_into_tools_that_need_it():
    registry = ToolRegistry()
    received = {}

    def handler(incident_sys_id, execution_id, action, status, agent=None, result=None, error=None):
        received.update(
            incident_sys_id=incident_sys_id,
            execution_id=execution_id,
            action=action,
            status=status,
        )
        return "logged"

    registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE, handler)
    result = registry.dispatch(
        "write_execution_log",
        "exec-42",
        incident_sys_id="sys-abc",
        action="check_status",
        status="started",
    )
    assert result == "logged"
    assert received["execution_id"] == "exec-42"
    assert received["incident_sys_id"] == "sys-abc"


def test_dispatch_logs_attempt_before_execution(caplog):
    registry = ToolRegistry()
    handler_called = []

    def handler(execution_id=None):
        handler_called.append(True)
        return "ok"

    registry.register("probe", PermissionClass.READ, handler)
    with caplog.at_level(logging.INFO, logger="src.agent.tools.registry"):
        registry.dispatch("probe", "exec-probe")
    assert any("Dispatch attempt" in record.message for record in caplog.records)
    assert handler_called == [True]


def test_default_registry_registers_five_tools_with_expected_classes():
    from src.servicenow.client import IncidentGateway

    registry = build_default_registry()
    expected = {
        "read_incident": PermissionClass.READ,
        "write_execution_log": PermissionClass.LOW_RISK_WRITE,
        "write_ai_fields": PermissionClass.LOW_RISK_WRITE,
        "write_work_note": PermissionClass.LOW_RISK_WRITE,
        "kb_write_back": PermissionClass.HIGH_RISK,
    }
    assert set(registry._tools) == set(expected)
    for name, permission_class in expected.items():
        assert registry._tools[name][0] is permission_class
        assert isinstance(registry._tools[name][1], str) is False
        assert callable(registry._tools[name][1])


def test_default_registry_handlers_are_incident_gateway_methods():
    from src.servicenow.client import IncidentGateway

    gateway = IncidentGateway()
    for name, (permission_class, handler) in build_default_registry()._tools.items():
        assert isinstance(handler.__self__, IncidentGateway)
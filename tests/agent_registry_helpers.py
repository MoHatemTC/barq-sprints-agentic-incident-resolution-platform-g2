"""Helpers for exercising agent nodes against a fake ServiceNow network.

The agent nodes no longer hold a ServiceNowClient: they dispatch through the
tool registry, and IncidentGateway is the only thing allowed to touch the
client. Tests therefore inject at the *registry* seam rather than swapping the
client out from under a node.

That is a stronger fake than the old one. These helpers build a real
ToolRegistry over a real IncidentGateway, with only the innermost client
replaced, so a test still exercises registration, the permission check and the
handler signatures -- the parts a `lambda **kwargs` stub would skip, and the
part that hid the dispatch/execution_id mismatch.
"""

from typing import Any, Dict, List, Optional

from src.agent.tools.permissions import PermissionClass
from src.agent.tools.registry import ToolRegistry
from src.servicenow.client import IncidentGateway

# Mirrors build_default_registry(). Kept explicit rather than derived from it so
# a test fails loudly if a tool is added to the default registry and not here.
READ_TOOLS = ("read_incident", "find_execution_log")
WRITE_TOOLS = ("write_ai_fields", "write_execution_log", "write_work_note")


class FakeServiceNowClient:
    """Records every Table API call the gateway makes, in order."""

    def __init__(self, get_incident_result: Optional[Dict[str, Any]] = None) -> None:
        self.get_incident_result = (
            {} if get_incident_result is None else get_incident_result
        )
        self.incident_patches: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.work_notes: List[Dict[str, Any]] = []
        self.calls: List[str] = []

    # -- reads ---------------------------------------------------------------
    def get_incident(self, sys_id):
        self.calls.append("get_incident")
        return dict(self.get_incident_result)

    def find_execution_log(self, execution_id, action):
        self.calls.append("find_execution_log")
        return next(
            (
                row
                for row in self.logs
                if row["execution_id"] == execution_id and row["action"] == action
            ),
            None,
        )

    # -- writes --------------------------------------------------------------
    def update_incident(self, sys_id, fields):
        self.calls.append("update_incident")
        self.incident_patches.append({"sys_id": sys_id, **fields})
        return fields

    def write_execution_log(self, incident_sys_id, execution_id, action, status,
                            agent=None, result=None, error=None):
        self.calls.append("write_execution_log")
        row = {
            "incident": incident_sys_id,
            "execution_id": execution_id,
            "action": action,
            "status": status,
            "agent": agent,
            "result": result,
        }
        self.logs.append(row)
        return row

    def add_work_note(self, sys_id, note):
        self.calls.append("add_work_note")
        self.work_notes.append({"sys_id": sys_id, "note": note})
        return {"note": note}


def build_registry(
    client: Optional[FakeServiceNowClient] = None,
    *,
    tools: Optional[Dict[str, str]] = None,
) -> ToolRegistry:
    """Build a real ToolRegistry over a real IncidentGateway.

    ``tools`` optionally overrides the registered set, keyed by tool name, to
    either a permission class name or the sentinel ``"absent"`` to leave the
    tool unregistered. Used to prove that act_node halts when a tool it needs
    is missing or unapproved.
    """
    gateway = IncidentGateway(client=client if client is not None else FakeServiceNowClient())
    handlers = {
        "read_incident": (PermissionClass.READ, gateway.read_incident),
        "find_execution_log": (PermissionClass.READ, gateway.find_execution_log),
        "write_ai_fields": (PermissionClass.LOW_RISK_WRITE, gateway.write_ai_fields),
        "write_execution_log": (PermissionClass.LOW_RISK_WRITE, gateway.write_execution_log),
        "write_work_note": (PermissionClass.LOW_RISK_WRITE, gateway.write_work_note),
    }

    overrides = tools or {}
    registry = ToolRegistry()
    for name, (permission_class, handler) in handlers.items():
        if name not in overrides:
            registry.register(name, permission_class, handler)
            continue
        replacement = overrides[name]
        if replacement == "absent":
            continue
        registry.register(name, PermissionClass[replacement], handler)
    return registry


def install_registry(monkeypatch, node_module: str, registry: ToolRegistry) -> None:
    """Point an agent node module at ``registry``."""
    monkeypatch.setattr(f"{node_module}.TOOL_REGISTRY", registry, raising=True)

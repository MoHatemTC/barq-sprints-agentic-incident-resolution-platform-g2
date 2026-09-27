"""Server-side tool registry enforcing permission-based access control.

Every handler invocation the agent makes must go through ToolRegistry.dispatch,
which is the only allowed path to the registered IncidentGateway handlers
(enforced by an import-boundary test). dispatch refuses unknown tools and
unapproved (HIGH_RISK) tools with a typed ToolRefusal before any handler runs.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from src.agent.tools.permissions import PermissionClass, is_approved

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRefusal:
    tool: str
    reason: str
    message: str = ""
    execution_id: Optional[str] = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tuple[PermissionClass, Callable]] = {}

    def register(self, name: str, permission_class: PermissionClass, handler: Callable) -> None:
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        if not callable(handler):
            raise TypeError(f"Handler for tool '{name}' is not callable")
        if not isinstance(permission_class, PermissionClass):
            raise TypeError(f"Permission class for tool '{name}' is not a PermissionClass")
        self._tools[name] = (permission_class, handler)
        logger.info(
            "Registered tool=%s permission_class=%s", name, permission_class.name
        )

    def dispatch(self, name: str, execution_id: str, **kwargs: Any):
        entry = self._tools.get(name)
        if entry is None:
            return ToolRefusal(
                tool=name,
                reason="unregistered_tool",
                message=f"No tool registered under name '{name}'.",
                execution_id=execution_id,
            )

        permission_class, handler = entry
        logger.info(
            "Dispatch attempt: tool=%s execution_id=%s permission_class=%s",
            name,
            execution_id,
            permission_class.name,
        )

        if not is_approved(execution_id, permission_class):
            refusal = ToolRefusal(
                tool=name,
                reason="approval_required",
                message=(
                    f"Tool '{name}' requires an approved, unconsumed approval "
                    f"record for execution '{execution_id}'."
                ),
                execution_id=execution_id,
            )
            logger.warning("Refused dispatch: %s", refusal)
            return refusal

        return handler(execution_id=execution_id, **kwargs)


def build_default_registry() -> ToolRegistry:
    from src.servicenow.client import IncidentGateway

    gateway = IncidentGateway()

    registry = ToolRegistry()
    registry.register("read_incident", PermissionClass.READ, gateway.read_incident)
    # Idempotency probe, not a write: act_node must be able to ask "has this
    # execution already written its receipt?" without an approval, or a
    # retried execution would duplicate the write S3.4 works to prevent.
    registry.register("find_execution_log", PermissionClass.READ, gateway.find_execution_log)
    registry.register("write_execution_log", PermissionClass.LOW_RISK_WRITE, gateway.write_execution_log)
    registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, gateway.write_ai_fields)
    registry.register("write_work_note", PermissionClass.LOW_RISK_WRITE, gateway.write_work_note)
    registry.register("kb_write_back", PermissionClass.HIGH_RISK, gateway.kb_write_back)
    return registry


DEFAULT_TOOL_REGISTRY = build_default_registry()
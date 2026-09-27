"""Server-side tool registry enforcing permission-based access control.

<<<<<<< HEAD
Every protected handler invocation must go through ToolRegistry.dispatch.
HIGH_RISK tools require an approved, unconsumed approval for the execution
before their handler is allowed to run.
"""

=======
Every handler invocation the agent makes must go through ToolRegistry.dispatch,
which is the only allowed path to the registered IncidentGateway handlers
(enforced by an import-boundary test). dispatch refuses unknown tools and
unapproved (HIGH_RISK) tools with a typed ToolRefusal before any handler runs.
"""

import inspect
>>>>>>> origin/development
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

<<<<<<< HEAD
    def register(
        self,
        name: str,
        permission_class: PermissionClass,
        handler: Callable,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        if not callable(handler):
            raise TypeError(
                f"Handler for tool '{name}' is not callable"
            )

        if not isinstance(permission_class, PermissionClass):
            raise TypeError(
                f"Permission class for tool '{name}' "
                "is not a PermissionClass"
            )

        self._tools[name] = (permission_class, handler)

        logger.info(
            "Registered tool=%s permission_class=%s",
            name,
            permission_class.name,
        )

    def dispatch(
        self,
        name: str,
        execution_id: str,
        **kwargs: Any,
    ):
        entry = self._tools.get(name)

=======
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
>>>>>>> origin/development
        if entry is None:
            return ToolRefusal(
                tool=name,
                reason="unregistered_tool",
                message=f"No tool registered under name '{name}'.",
                execution_id=execution_id,
            )

        permission_class, handler = entry
<<<<<<< HEAD

        logger.info(
            "Dispatch attempt: tool=%s execution_id=%s "
            "permission_class=%s",
=======
        logger.info(
            "Dispatch attempt: tool=%s execution_id=%s permission_class=%s",
>>>>>>> origin/development
            name,
            execution_id,
            permission_class.name,
        )

        if not is_approved(execution_id, permission_class):
            refusal = ToolRefusal(
                tool=name,
                reason="approval_required",
                message=(
<<<<<<< HEAD
                    f"Tool '{name}' requires an approved, "
                    f"unconsumed approval record for execution "
                    f"'{execution_id}'."
                ),
                execution_id=execution_id,
            )

            logger.warning("Refused dispatch: %s", refusal)
            return refusal

        return handler(
            execution_id=execution_id,
            **kwargs,
        )


def _kb_write_back_handler(
    *,
    execution_id: str,
    incident_snapshot: dict,
    human_solution: str,
    article_number: str,
    category: str,
    service: str,
    security_level: str,
    db: Any,
) -> dict:
    """Execute the existing S3.5 knowledge-capture pipeline.

    The registry owns the HIGH_RISK authorization gate. This handler only
    delegates to the existing knowledge-capture implementation and does
    not introduce a second ServiceNow/Qdrant publishing path.
    """

    from src.agent.knowledge_capture import capture_human_resolution

    return capture_human_resolution(
        incident_snapshot=incident_snapshot,
        human_solution=human_solution,
        article_number=article_number,
        category=category,
        service=service,
        security_level=security_level,
        execution_identifier=execution_id,
        db=db,
    )


def build_default_registry() -> ToolRegistry:
    from src.servicenow.client import ServiceNowClient

    client = ServiceNowClient()

    registry = ToolRegistry()

    # Existing ServiceNow operations
    registry.register(
        "write_execution_log",
        PermissionClass.LOW_RISK_WRITE,
        client.write_execution_log,
    )

    registry.register(
        "write_ai_fields",
        PermissionClass.LOW_RISK_WRITE,
        client.update_incident,
    )

    registry.register(
        "write_work_note",
        PermissionClass.LOW_RISK_WRITE,
        client.add_work_note,
    )

    # S3.5 knowledge write-back.
    #
    # This is intentionally routed through the registry because it is
    # persistent external knowledge creation and therefore HIGH_RISK.
    registry.register(
        "kb_write_back",
        PermissionClass.HIGH_RISK,
        _kb_write_back_handler,
    )

=======
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
>>>>>>> origin/development
    return registry


DEFAULT_TOOL_REGISTRY = build_default_registry()
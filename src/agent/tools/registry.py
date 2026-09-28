"""Server-side tool registry enforcing permission-based access control.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from src.agent.tools.permissions import PermissionClass, is_approved

try:
    from src.observability.tracing import get_client as _get_lf_client, _current_trace_id, _current_span_id
    _TRACING = True
except Exception:
    _TRACING = False

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

        return self._call_with_span(name, execution_id, handler, **kwargs)

    def _call_with_span(self, name: str, execution_id: str, handler: Callable, **kwargs):
        """Call handler wrapped in a 'servicenow.<name>' child span for Langfuse tree."""
        if not _TRACING:
            return handler(execution_id=execution_id, **kwargs)

        try:
            client = _get_lf_client()
            trace_id = _current_trace_id.get()
            parent_span_id = _current_span_id.get()

            trace_context = None
            if trace_id:
                trace_context = {"trace_id": trace_id}
                if parent_span_id:
                    trace_context["parent_span_id"] = parent_span_id

            span = client.start_observation(
                name=f"servicenow.{name}",
                as_type="span",
                trace_context=trace_context,
            ) if trace_id else None
        except Exception:
            span = None

        try:
            result = handler(execution_id=execution_id, **kwargs)
            if span:
                try:
                    span.update(level="DEFAULT", status_message="ok")
                    span.end()
                except Exception:
                    pass
            return result
        except BaseException as e:
            if span:
                try:
                    # If it's a known non-fatal write mismatch, mark as WARNING
                    if type(e).__name__ == "ServiceNowWriteNotAppliedError":
                        span.update(level="WARNING", status_message=f"Write verification mismatch: {e}")
                    else:
                        span.update(level="ERROR", status_message=f"{type(e).__name__}: {e}")
                    span.end()
                except Exception:
                    pass
            raise

    def permission_class_of(self, name: str) -> Optional[PermissionClass]:
        """Return the PermissionClass for a registered tool, or None if unregistered.

        Does not expose _tools directly.
        """
        entry = self._tools.get(name)
        return entry[0] if entry else None


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
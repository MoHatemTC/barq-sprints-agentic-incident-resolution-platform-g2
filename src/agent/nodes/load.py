import logging
from dataclasses import asdict
from typing import Dict, Any
from src.observability.tracing import trace_node
from src.agent.guardrails.input_screening import screen_incident_payload
from src.agent.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRefusal

logger = logging.getLogger(__name__)

# Registry seam; see src/agent/nodes/act.py for why this is a module attribute.
TOOL_REGISTRY = DEFAULT_TOOL_REGISTRY

@trace_node(name="load")
def load_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch full incident from ServiceNow via OAuth using event identifiers.

    After loading the payload, the incident text is run through the input
    guardrails (injection screening + PII/credential redaction).  The
    screening metadata is recorded on ``incident_payload["_screening"]``
    so downstream nodes and audit systems can inspect it without ever
    seeing the raw sensitive content.

    Incidents are **never** silently dropped — even flagged payloads
    proceed with neutralised / redacted text, and so does a payload whose
    ServiceNow fetch was refused by the tool registry.
    """
    incident_number = state.get("incident_number")
    payload = state.get("incident_payload", {}).copy()
    sys_id = payload.get("sys_id")

    if sys_id:
        # Read through the registry, not the raw client: the perimeter test
        # forbids ServiceNowClient outside IncidentGateway, and routing the
        # fetch through dispatch means the READ permission class and the
        # unregistered-tool check apply to it like any other agent read.
        try:
            fetched_payload = TOOL_REGISTRY.dispatch(
                "read_incident", state.get("execution_id"), sys_id=sys_id
            )
            if isinstance(fetched_payload, ToolRefusal):
                # load has always been tolerant of a failed fetch -- the
                # incident still proceeds on the payload it arrived with --
                # so a refusal degrades the same way and is not fatal.
                logger.warning(
                    "Registry refused read_incident for %s (sys_id=%s): %s (%s)",
                    incident_number, sys_id,
                    fetched_payload.reason, fetched_payload.message,
                )
            elif fetched_payload and isinstance(fetched_payload, dict):
                payload.update(fetched_payload)
                payload["sys_id"] = sys_id
        except Exception as e:
            logger.warning(f"Could not fetch incident {incident_number} (sys_id={sys_id}): {e}")
    else:
        logger.warning(f"No sys_id in payload for incident {incident_number}")

    # ── Input Guardrails (FR-18) ──────────────────────────────────────
    # Screen for prompt-injection patterns and redact credentials / PII
    # before the payload reaches any downstream LLM prompt path.
    screened_payload, screening_meta = screen_incident_payload(payload)

    # Record audit metadata on the payload (no raw sensitive strings).
    screened_payload["_screening"] = asdict(screening_meta)

    screened_payload["status"] = "loaded"
    return {"incident_payload": screened_payload}

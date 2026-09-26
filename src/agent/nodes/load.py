import logging
from dataclasses import asdict
from typing import Dict, Any
from src.observability.tracing import trace_node
from src.servicenow.client import ServiceNowClient
from src.agent.guardrails.input_screening import screen_incident_payload

logger = logging.getLogger(__name__)

@trace_node(name="load")
def load_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch full incident from ServiceNow via OAuth using event identifiers.

    After loading the payload, the incident text is run through the input
    guardrails (injection screening + PII/credential redaction).  The
    screening metadata is recorded on ``incident_payload["_screening"]``
    so downstream nodes and audit systems can inspect it without ever
    seeing the raw sensitive content.

    Incidents are **never** silently dropped — even flagged payloads
    proceed with neutralised / redacted text.
    """
    incident_number = state.get("incident_number")
    payload = state.get("incident_payload", {}).copy()
    sys_id = payload.get("sys_id")

    if sys_id:
        try:
            client = ServiceNowClient()
            fetched_payload = client.get_incident(sys_id)
            if fetched_payload and isinstance(fetched_payload, dict):
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

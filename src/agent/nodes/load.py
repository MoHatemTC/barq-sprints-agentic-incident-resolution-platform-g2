import logging
from typing import Dict, Any
from src.observability.tracing import trace_node
from src.servicenow.client import ServiceNowClient

logger = logging.getLogger(__name__)

@trace_node(name="load")
def load_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch full incident from ServiceNow via OAuth using event identifiers."""
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

    payload["status"] = "loaded"
    return {"incident_payload": payload}


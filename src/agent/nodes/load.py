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
    
    if incident_number:
        try:
            client = ServiceNowClient()
            fetched_payload = client.get_incident(incident_number)
            if fetched_payload and isinstance(fetched_payload, dict):
                payload.update(fetched_payload)
        except Exception as e:
            logger.warning(f"Could not fetch incident {incident_number} from ServiceNow: {e}")

    payload["status"] = "loaded"
    if incident_number:
        payload["sys_id"] = incident_number
    
    return {"incident_payload": payload}

import uuid
import logging
from datetime import datetime, timezone

import requests

from . import config
from .auth import TokenManager
from .exceptions import ServiceNowError, raise_for_status

logger = logging.getLogger(__name__)


class ServiceNowClient:
    # OAuth authenticated Table API client

    def __init__(self):
        self._tokens = TokenManager()
    
    @staticmethod
    def new_execution_id():
        return str(uuid.uuid4()) # for logs

    def _request(self, method, url, **kwargs):
        # Send a request, refreshing the token once on 401
        response = requests.request(
            method,
            url,
            headers=self._tokens.headers(),
            timeout=config.TIMEOUT,
            **kwargs,
        )

        if response.status_code == 401:
            self._tokens.invalidate()
            response = requests.request(
                method,
                url,
                headers=self._tokens.headers(),
                timeout=config.TIMEOUT,
                **kwargs,
            )

        raise_for_status(response)
        return response.json().get("result")
    
    def get_incident(self, sys_id):
         # Read one incident by sys_id
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        return self._request("GET", url)

    
    def update_incident(self, sys_id, fields):
        # Write AI fields , Keys are logical names from config file
        payload = {}
        for key, value in fields.items():
            if key not in config.AI_FIELDS:
                raise ValueError(f"Unknown AI field: {key}")
            payload[config.AI_FIELDS[key]] = value

        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        return self._request("PATCH", url, json=payload)
    
    

    def add_work_note(self, sys_id, note):
        # Append a work note to incident
        if not note or not note.strip():
            raise ValueError("Work note cannot be empty")

        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        return self._request("PATCH", url, json={config.WORK_NOTES: note})
    
    
    # audit logger for debugging 
    def write_execution_log(self, incident_sys_id, execution_id, action,
                            status, agent=None, result=None, error=None):
        # Insert one execution log record & Never raises: audit failure 
        payload = {
            config.LOG_FIELDS["incident"]: incident_sys_id,
            config.LOG_FIELDS["execution_id"]: execution_id,
            config.LOG_FIELDS["action"]: action,
            config.LOG_FIELDS["status"]: status,
            config.LOG_FIELDS["timestamp"]: datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        if agent:
            payload[config.LOG_FIELDS["agent"]] = agent
        if result:
            payload[config.LOG_FIELDS["result"]] = result[:4000]
        if error:
            payload[config.LOG_FIELDS["error"]] = error[:4000]

        url = f"{config.TABLE_API}/{config.EXECUTION_LOG_TABLE}"
        
        try:
            return self._request("POST", url, json=payload)
        except (ServiceNowError, requests.RequestException) as exc:
            logger.warning(
                "Execution log write failed for %s: %s", execution_id, type(exc).__name__
            )
            return None
    
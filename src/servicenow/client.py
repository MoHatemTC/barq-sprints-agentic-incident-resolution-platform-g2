import uuid
import logging
from datetime import datetime, timezone

import requests

from . import config
from .auth import TokenManager
from .exceptions import ServiceNowAuthError, ServiceNowError, raise_for_status

logger = logging.getLogger(__name__)


class ServiceNowClient:
    # OAuth authenticated Table API client

    def __init__(self):
        self._tokens = TokenManager()

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
    
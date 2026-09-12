import uuid
import logging
from datetime import datetime, timezone

import requests

from . import config
from .auth import TokenManager
from .exceptions import (
    ServiceNowError,
    ServiceNowWriteNotAppliedError,
    raise_for_status,
)

logger = logging.getLogger(__name__)


def _same(sent, got):
    # ServiceNow returns every value as string
    if sent is None:
        return got in ("", None)
    if isinstance(sent, bool):
        return str(got).lower() == str(sent).lower()
    if isinstance(sent, (int, float)):
        try:
            return float(got) == float(sent)
        except (TypeError, ValueError):
            return False
    return str(got) == str(sent)

class ServiceNowClient:
    # OAuth authenticated Table API client

    def __init__(self):
        self._tokens = TokenManager()

    @staticmethod
    def new_execution_id():
        return str(uuid.uuid4())  # for logs

    
    
    def _send(self, method, url, **kwargs):
        return requests.request(
            method,
            url,
            headers=self._tokens.headers(),
            timeout=config.TIMEOUT,
            **kwargs,
        )

    
    
    def _request(self, method, url, **kwargs):
        # Send a request, refreshing the token once on 401
        response = self._send(method, url, **kwargs)

        if response.status_code == 401:
            self._tokens.invalidate()
            response = self._send(method, url, **kwargs)

        raise_for_status(response)
        return response.json().get("result")

    
    
    def get_incident(self, sys_id):
        # Read one incident by sys_id
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        return self._request("GET", url)

    
    
    def update_incident(self, sys_id, fields):
        # Write AI fields, keys are logical names from config file
        payload = {}
        for key, value in fields.items():
            if key not in config.AI_FIELDS:
                raise ValueError(f"Unknown AI field: {key}")
            payload[config.AI_FIELDS[key]] = value

        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        result = self._request("PATCH", url, json=payload)

        # 200 does not prove the write landed, compare sent against returned
        dropped = [c for c, v in payload.items() if not _same(v, result.get(c))]
        if dropped:
            raise ServiceNowWriteNotAppliedError(200, f"Fields not written: {dropped}")
        return result

    
    
    def add_work_note(self, sys_id, note):
        # Append a work note to incident
        if not note or not note.strip():
            raise ValueError("Work note cannot be empty")

        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        result = self._request("PATCH", url, json={config.WORK_NOTES: note})

        # neither the PATCH response nor a GET on the incident exposes the jornal
        check = self._request(
            "GET",
            f"{config.TABLE_API}/sys_journal_field",
            params={
                "sysparm_query": f"element_id={sys_id}^element=work_notes"
                                 f"^ORDERBYDESCsys_created_on",
                "sysparm_limit": "1",
                "sysparm_fields": "value",
            },
        )
        if not check or note not in check[0].get("value", ""):
            raise ServiceNowWriteNotAppliedError(200, "Work note not applied")
        return result

    # audit logs one record per attempt, including failures
    
    
    def write_execution_log(self, incident_sys_id, execution_id, action,
                            status, agent=None, result=None, error=None):
        # Never raises on a ServiceNow failure: audit must not break the caller
        if status not in config.VALID_LOG_STATUSES:
            raise ValueError(f"Invalid log status: {status}")

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
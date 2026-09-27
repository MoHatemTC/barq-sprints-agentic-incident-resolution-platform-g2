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

    def _response(self, method, url, **kwargs):
        # Send a request, refreshing the token once on 401
        response = self._send(method, url, **kwargs)

        if response.status_code == 401:
            self._tokens.invalidate()
            response = self._send(method, url, **kwargs)

        raise_for_status(response)
        return response

    def _request(self, method, url, **kwargs):
        return self._response(method, url, **kwargs).json().get("result")

    def get_incident(self, sys_id):
        # Read one incident by sys_id
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        return self._request("GET", url)

    def create_incident(self, short_description, description=None, caller_id=None):
        # Create a new incident, returns the created record (sys_id, number, ...)
        payload = {"short_description": short_description}
        if description:
            payload["description"] = description
        if caller_id:
            payload["caller_id"] = caller_id
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}"
        return self._request("POST", url, json=payload)

    def get_published_kb_articles(self, page_size=100):
        # Read all published articles of our KB (SERVICENOW_KB_SYS_ID), page by page.
        # ServiceNow drops rows hidden by ACLs *after* applying the limit, so a
        # short page is not the last page: stop on X-Total-Count instead.
        url = f"{config.TABLE_API}/kb_knowledge"
        query = "workflow_state=published"
        if config.KB_SYS_ID:
            query += f"^kb_knowledge_base={config.KB_SYS_ID}"
        articles, offset = [], 0
        while True:
            response = self._response(
                "GET",
                url,
                params={
                    "sysparm_query": f"{query}^ORDERBYsys_id",
                    "sysparm_limit": page_size,
                    "sysparm_offset": offset,
                },
            )
            batch = response.json().get("result") or []
            articles.extend(batch)
            offset += page_size
            total = response.headers.get("X-Total-Count")
            if (int(total) <= offset) if total is not None else not batch:
                return articles

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

        # neither the PATCH response nor a GET on the incident exposes the journal
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

    def find_execution_log(self, execution_id, action):
        # Existing log row for this execution + action, or None.
        # act writes its log row last, so this row doubles as the "write done" receipt.
        url = f"{config.TABLE_API}/{config.EXECUTION_LOG_TABLE}"
        rows = self._request(
            "GET",
            url,
            params={
                "sysparm_query": f"{config.LOG_FIELDS['execution_id']}={execution_id}"
                                 f"^{config.LOG_FIELDS['action']}={action}",
                "sysparm_limit": "1",
            },
        )
        return rows[0] if rows else None

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


class IncidentGateway:
    """Server-side tool boundary for the agent's ServiceNow actions.

    Each method maps one registered tool to the underlying ServiceNowClient
    Table API operations. Handlers are only ever invoked through the
    ToolRegistry (registry.py) after the registration and permission checks
    pass; direct call sites outside the registry are rejected by the
    import-boundary test.

    Every handler accepts ``execution_id``. ``ToolRegistry.dispatch`` passes it
    on every call so the execution a write belongs to is available to the
    handler for audit, and so a handler's signature matches the one calling
    convention the registry uses. It is optional with a default because the
    registry supplies it, not the caller.
    """

    def __init__(self, client=None):
        self._client = client if client is not None else ServiceNowClient()

    def read_incident(self, sys_id, execution_id=None):
        return self._client.get_incident(sys_id)

    def find_execution_log(self, execution_id, action, incident_sys_id=None):
        """Return the prior receipt row for this (execution_id, action), if any.

        This is the idempotency probe act_node uses to avoid writing the
        outcome to ServiceNow twice, so it is a READ and needs no approval.
        S3.4 depends on it: without it a retried execution re-patches the
        incident and writes a second receipt row.
        """
        return self._client.find_execution_log(execution_id, action)

    def write_execution_log(self, incident_sys_id, execution_id, action, status,
                            agent=None, result=None, error=None):
        return self._client.write_execution_log(
            incident_sys_id,
            execution_id,
            action,
            status,
            agent=agent,
            result=result,
            error=error,
        )

    def write_ai_fields(self, sys_id, fields, execution_id=None):
        return self._client.update_incident(sys_id, fields)

    def write_work_note(self, sys_id, note, execution_id=None):
        return self._client.add_work_note(sys_id, note)

    def kb_write_back(self, corpus_path=None, dry_run=False, execution_id=None):
        from src.retrieval.publish_kb import publish

        return publish(corpus_path, dry_run=dry_run)

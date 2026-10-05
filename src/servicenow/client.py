import uuid
import logging
from datetime import datetime, timezone

import requests

from . import config, contracts
from .auth import TokenManager
from .exceptions import (
    ServiceNowError,
    ServiceNowNetworkError,
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
            # Round to 4 decimal places to avoid floating-point precision mismatches
            # (e.g. we send 0.83, ServiceNow stores and returns "0.83")
            return round(float(got), 4) == round(float(sent), 4)
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
        try:
            return requests.request(
                method,
                url,
                headers=self._tokens.headers(),
                timeout=config.TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ServiceNowNetworkError(0, str(exc)) from exc

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

    def create_incident(self, short_description, description=None, caller_id=None, category=None,
                        correlation_id=None):
        # Create a new incident, returns the created record (sys_id, number, ...)
        # correlation_id: the caller's request id, so a retry can find this record
        payload = {"short_description": short_description}
        if description:
            payload["description"] = description
        if caller_id:
            payload["caller_id"] = caller_id
        if category:
            payload["category"] = category
        if correlation_id:
            payload["correlation_id"] = correlation_id
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}"
        body = self._response("POST", url, json=payload).json()
        return contracts.result_record(body, "created incident", ("sys_id", "number"))

    def find_incident_by_correlation(self, correlation_id):
        # The incident created with this correlation_id (sys_id, number), or None
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}"
        body = self._response(
            "GET",
            url,
            params={
                "sysparm_query": f"correlation_id={correlation_id}",
                "sysparm_fields": "sys_id,number",
                "sysparm_limit": "1",
            },
        ).json()
        rows = contracts.result_list(body, "correlation lookup")
        if not rows:
            return None
        return contracts.result_record({"result": rows[0]}, "correlation lookup", ("sys_id", "number"))

    def get_choices(self, table, element):
        # Choices of one choice field exactly as the ServiceNow form lists them.
        # UI meta API, not sys_choice: the integration user cannot read sys_choice (403).
        url = f"{config.INSTANCE_URL}/api/now/ui/meta/{table}"
        choices = contracts.ui_meta_choices(self._response("GET", url).json(), element, f"UI meta {table}.{element}")
        return [
            {"label": c.get("label"), "value": c.get("value")}
            for c in choices
            if c.get("value")  # skip the form's "-- None --" entry
        ]

    def list_incidents(self, fields, limit=20, offset=0, query=None):
        # Newest incidents first, one page, optionally filtered by an encoded
        # query. Each field comes back as {"value": ..., "display_value": ...}.
        # Returns (rows, total).
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}"
        order = "ORDERBYDESCsys_created_on"
        response = self._response(
            "GET",
            url,
            params={
                "sysparm_query": f"{query}^{order}" if query else order,
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": limit,
                "sysparm_offset": offset,
            },
        )
        rows = contracts.result_list(response.json(), "incident list")
        total = contracts.total_count(response.headers, "incident list")
        return rows, total if total is not None else len(rows)

    def delete_incident(self, sys_id):
        """Delete an incident explicitly requested by the dashboard user."""
        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        # ServiceNow commonly returns 204 No Content for DELETE, so do not
        # route this through _request(), which assumes a JSON response body.
        response = self._response("DELETE", url)
        return {"status_code": response.status_code}

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
        # Standard SNOW fields (resolution tab) are non-critical; only custom AI fields are critical.
        STANDARD_SNOW_FIELDS = {"close_code", "close_notes", "resolved_by", "resolved_at", "state"}
        # Soft AI fields: custom fields that ServiceNow may coerce (e.g. confidence int→decimal)
        # or reject clearing (failure_reason=None on resolved incidents). Warn-only, never raise.
        SOFT_AI_FIELDS = {"confidence", "failure_reason"}

        payload = {}
        for key, value in fields.items():
            if key not in config.AI_FIELDS:
                raise ValueError(f"Unknown AI field: {key}")
            payload[config.AI_FIELDS[key]] = value

        url = f"{config.TABLE_API}/{config.INCIDENT_TABLE}/{sys_id}"
        result = self._request("PATCH", url, json=payload)

        # 200 does not prove the write landed, compare sent against returned.
        # Standard fields (close_code, state, etc.) are verified as warnings only
        # because ServiceNow may apply ACLs or coerce values on resolution fields.
        non_critical = STANDARD_SNOW_FIELDS | SOFT_AI_FIELDS
        critical_keys = {config.AI_FIELDS[k] for k in fields if k not in non_critical}
        soft_keys = {config.AI_FIELDS[k] for k in fields if k in non_critical}

        critical_dropped = [c for c in critical_keys if not _same(payload.get(c), result.get(c))]
        soft_dropped = [c for c in soft_keys if not _same(payload.get(c), result.get(c))]

        if soft_dropped:
            logger.warning(
                "Non-critical fields not confirmed by ServiceNow (type coercion or ACL): %s",
                soft_dropped,
            )
        if critical_dropped:
            raise ServiceNowWriteNotAppliedError(200, f"Fields not written: {critical_dropped}")
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
        if not check or not check[0].get("value", ""):
            raise ServiceNowWriteNotAppliedError(200, "Work note not applied")
        # ServiceNow may reformat whitespace/newlines in journal entries —
        # compare using normalized (collapsed) text to avoid false negatives.
        stored_normalized = " ".join(check[0]["value"].split())
        note_normalized = " ".join(note.split())
        if note_normalized not in stored_normalized:
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
    """Server-side tool boundary for the agent's ServiceNow actions."""

    def __init__(self, client=None):
        self._client = client if client is not None else ServiceNowClient()

    def read_incident(self, sys_id, execution_id=None):
        return self._client.get_incident(sys_id)

    def list_incidents(self, query, fields, limit=100, execution_id=None):
        """Incidents matching an encoded query, newest first, one page"""
        rows, _total = self._client.list_incidents(fields, limit=limit, query=query)
        return rows

    def find_execution_log(self, execution_id, action, incident_sys_id=None):
        """Return the prior receipt row for this (execution_id, action), if any"""
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

    def kb_write_back(self, execution_id=None, **capture):
        """publish an approved human resolution as a KB article"""
        from src.agent.knowledge_capture import capture_human_resolution

        return capture_human_resolution(execution_identifier=execution_id, **capture)

"""Production composition for the published S2.2 and S2.5 worker APIs.

The imports are deliberately lazy: Sprint branches are developed separately,
but the final worker process imports the published S2.2/S2.5 modules once
those branches are present in its deployed source tree.  This module does not
copy persistence services or graph nodes.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from typing import Protocol


logger = logging.getLogger(__name__)


def execution_status_for(result: object) -> str:
    """S3.4: a graph that paused at interrupt() is awaiting approval, not finished."""
    if isinstance(result, Mapping) and result.get("__interrupt__"):
        return "awaiting_approval"
    return "succeeded"


@dataclass(frozen=True)
class ExecutionContext:
    """S2.2-generated identifiers carried internally in Celery headers."""

    execution_identifier: str
    retry_state_id: int

    def task_headers(self) -> dict[str, str | int]:
        return {
            "barq_execution_identifier": self.execution_identifier,
            "barq_retry_state_id": self.retry_state_id,
        }


class StateManagerFactory(Protocol):
    def __call__(self) -> tuple[object, Callable[[], None]]:
        """Return a StateManager and a function that closes its DB session."""


def load_s2_2_state_manager() -> tuple[object, Callable[[], None]]:
    """Construct the actual published S2.2 StateManager with SessionLocal."""
    try:
        database = import_module("src.db.database")
        state_manager_module = import_module("src.orchestrator.state_manager")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "S2.2 runtime modules are required for the integrated worker"
        ) from error

    db = database.SessionLocal()
    return state_manager_module.StateManager(db), db.close


def establish_execution_context(
    accepted_incident: Mapping[str, object],
    *,
    state_manager_factory: StateManagerFactory = load_s2_2_state_manager,
) -> ExecutionContext:
    """Create one S2.2 Execution and its RetryState for a valid list event."""
    incident_number = accepted_incident.get("number")
    if not isinstance(incident_number, str) or not incident_number.strip():
        raise ValueError("accepted incident number must be a non-empty string")

    state_manager, close = state_manager_factory()
    try:
        execution = state_manager.create_execution(
            incident_reference=incident_number,
            agent_version=os.environ.get("BARQ_AGENT_VERSION", "sprint-3.1"),
            model_name=os.environ.get("LLM_MODEL", "gemini-3.6-flash"),
        )
        execution_identifier = getattr(execution, "execution_identifier", None)
        if not isinstance(execution_identifier, str) or not execution_identifier:
            raise RuntimeError("S2.2 create_execution returned no execution_identifier")

        retry_state = state_manager.create_retry_state(
            execution_reference=execution_identifier,
            attempt_count=0,
        )
        retry_state_id = getattr(retry_state, "id", None)
        if isinstance(retry_state_id, bool) or not isinstance(retry_state_id, int):
            raise RuntimeError("S2.2 create_retry_state returned no integer id")
        return ExecutionContext(execution_identifier, retry_state_id)
    finally:
        close()


@dataclass(frozen=True)
class StateManagerTaskRecorder:
    """Use S2.2 APIs for attempts, failures, and final worker outcomes."""

    context: ExecutionContext
    failing_node: str
    state_manager_factory: StateManagerFactory = load_s2_2_state_manager

    def record_retry(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        del accepted_incident
        state_manager, close = self.state_manager_factory()
        try:
            state_manager.update_retry_state(
                retry_state_id=self.context.retry_state_id,
                attempt_count=retries_completed + 1,
                last_error=str(error),
                next_attempt_time=None,
            )
        finally:
            close()

    def record_failure(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        del accepted_incident
        state_manager, close = self.state_manager_factory()
        try:
            state_manager.record_failure(
                execution_reference=self.context.execution_identifier,
                failing_node=self.failing_node,
                error_class=type(error).__name__,
                message=str(error),
                retry_count=retries_completed,
            )
            state_manager.update_execution_status(
                self.context.execution_identifier,
                "failed",
            )
        finally:
            close()

    def record_success(self, accepted_incident: object, result: object) -> None:
        state_manager, close = self.state_manager_factory()
        try:
            checkpoint = _dashboard_checkpoint(result)
            state_manager.save_checkpoint(
                execution_reference=self.context.execution_identifier,
                node_name=_terminal_node_name(checkpoint),
                checkpoint=json.dumps(checkpoint, default=str),
            )
            _update_node_reached(
                state_manager,
                self.context.execution_identifier,
                _terminal_node_name(checkpoint),
            )
            execution = state_manager.update_execution_status(
                self.context.execution_identifier,
                execution_status_for(result),
            )
            get_retry_state = getattr(state_manager, "get_retry_state", None)
            retry_state = (
                get_retry_state(self.context.execution_identifier)
                if callable(get_retry_state)
                else None
            )
            execution_metadata = _execution_metadata(execution, retry_state)
        finally:
            close()

        # S3.4: a paused run must not write to ServiceNow before the human decides;
        # act writes after the approval resumes it
        if execution_status_for(result) == "awaiting_approval":
            return
        _sync_servicenow_completion(
            accepted_incident,
            checkpoint,
            self.context.execution_identifier,
            execution_metadata,
        )


class GraphAgentExecutor:
    """Thin S2.3 adapter for S2.5's published GraphAgentExecutor behavior."""

    def execute(
        self,
        accepted_incident: Mapping[str, object],
        execution_id: str | None = None,
        incident_number: str | None = None,
    ) -> object:
        """Invoke the published S2.5 graph with its documented arguments."""
        try:
            graph_module = import_module("src.agent.graph")
            checkpointer_module = import_module("src.agent.checkpointer")
            tracing_module = import_module("src.observability.tracing")
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "S2.5 agent modules are required for the integrated worker"
            ) from error

        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("S2.2 execution identifier is required for S2.5")
        if not isinstance(incident_number, str) or not incident_number:
            raise ValueError("incident number is required for S2.5")

        def invoke_graph(
            *,
            accepted_incident: Mapping[str, object],
            execution_id: str,
            incident_number: str,
        ) -> object:
            graph = graph_module.compile_graph(
                checkpointer=checkpointer_module.get_checkpointer()
            )
            return graph.invoke(
                {
                    "execution_id": execution_id,
                    "incident_number": incident_number,
                    "incident_payload": accepted_incident,
                },
                config={"configurable": {"thread_id": execution_id}},
            )

        traced_invoke = tracing_module.trace_execution("execute_incident_graph")(
            invoke_graph
        )
        return traced_invoke(
            accepted_incident=accepted_incident,
            execution_id=execution_id,
            incident_number=incident_number,
        )


def context_from_task_headers(headers: object) -> ExecutionContext | None:
    """Read S2.2 context propagated by the Redis consumer, not the payload."""
    if not isinstance(headers, Mapping):
        return None
    execution_identifier = headers.get("barq_execution_identifier")
    retry_state_id = headers.get("barq_retry_state_id")
    if not isinstance(execution_identifier, str) or not execution_identifier:
        return None
    if isinstance(retry_state_id, bool) or not isinstance(retry_state_id, int):
        return None
    return ExecutionContext(execution_identifier, retry_state_id)


def _dashboard_checkpoint(result: object) -> dict[str, object]:
    if not isinstance(result, Mapping):
        return {"result": result}

    checkpoint = dict(result)
    checkpoint.pop("incident_payload", None)
    return checkpoint


def _terminal_node_name(checkpoint: Mapping[str, object]) -> str:
    action = checkpoint.get("action_taken")
    if isinstance(action, str) and action:
        return action[:255]

    if checkpoint.get("human_review_required") is True:
        return "human_review_required"

    if checkpoint.get("outputs"):
        return "resolved"

    return "graph_completed"


def _sync_servicenow_completion(
    accepted_incident: object,
    checkpoint: Mapping[str, object],
    execution_identifier: str,
    execution_metadata: Mapping[str, object],
) -> None:
    """Reflect a completed BARQ run in its ServiceNow AI fields.

    A ServiceNow outage must not turn an otherwise completed diagnosis into a
    failed worker task. The durable BARQ checkpoint remains the source for
    retrying or auditing the result.
    """
    if not isinstance(accepted_incident, Mapping):
        return
    incident_sys_id = accepted_incident.get("sys_id")
    if not isinstance(incident_sys_id, str) or not incident_sys_id:
        return

    fields = _servicenow_completion_fields(checkpoint, execution_metadata)
    try:
        # Through the registry, like every other agent-side ServiceNow write:
        # this is the same incident the act node writes, so it takes the same
        # permission path rather than reaching for a client of its own.
        registry_module = import_module("src.agent.tools.registry")
        result = registry_module.DEFAULT_TOOL_REGISTRY.dispatch(
            "write_ai_fields", execution_identifier, sys_id=incident_sys_id, fields=fields
        )
        if isinstance(result, registry_module.ToolRefusal):
            # Not an outage: the tool is unregistered or unapproved, and
            # retrying the same execution will not change that. Logged at error
            # so it cannot pass for a transient ServiceNow failure, and still
            # not raised, because a completed diagnosis must not be undone by a
            # bookkeeping write.
            logger.error(
                "BARQ execution %s completed but the registry refused "
                "write_ai_fields (%s): %s",
                execution_identifier, result.reason, result.message,
            )
    except Exception:
        logger.warning(
            "BARQ completed execution %s, but could not update its ServiceNow AI fields",
            execution_identifier,
            exc_info=True,
        )


def _execution_metadata(execution: object, retry_state: object) -> dict[str, object]:
    return {
        "processing_start": _servicenow_timestamp(getattr(execution, "started_at", None)),
        "processing_end": _servicenow_timestamp(
            getattr(execution, "ended_at", None) or datetime.now(timezone.utc)
        ),
        "retry_count": getattr(retry_state, "attempt_count", 0),
        "max_retries": _environment_non_negative_int("CELERY_TASK_MAX_RETRIES", 3),
        "agent_version": getattr(execution, "agent_version", None)
        or os.environ.get("BARQ_AGENT_VERSION", "sprint-3.1"),
        "model_name": getattr(execution, "model_name", None)
        or os.environ.get("LLM_MODEL", "gemini-3.6-flash"),
    }


def _servicenow_completion_fields(
    checkpoint: Mapping[str, object], execution_metadata: Mapping[str, object] | None = None
) -> dict[str, object]:
    execution_metadata = execution_metadata or {}
    outputs = checkpoint.get("outputs")
    outputs = outputs if isinstance(outputs, Mapping) else {}
    fields: dict[str, object] = {
        "processing_state": "complete",
        "processing_start": execution_metadata.get("processing_start"),
        "processing_end": execution_metadata.get("processing_end"),
        "max_retries": execution_metadata.get("max_retries", 3),
        "retry_count": execution_metadata.get("retry_count", 0),
        "retry_time_out": None,
        "agent_version": execution_metadata.get("agent_version", "sprint-3.1"),
        "model_name": execution_metadata.get("model_name", "gemini-3.6-flash"),
        "classification": _field_text(checkpoint.get("classification"), 255),
        "confidence": checkpoint.get("confidence") or 0,
        "suggestion": _field_text(outputs.get("diagnosis"), 4_000),
        "resolution": _field_text(outputs.get("resolution"), 4_000),
        "human_review": checkpoint.get("human_review_required") is True,
        "failure_reason": None,
    }
    failure_reason = _field_text(checkpoint.get("failure_reason"), 1_000)
    if failure_reason:
        fields["failure_reason"] = failure_reason
    return fields


def _field_text(value: object, max_length: int) -> str:
    text = value.strip() if isinstance(value, str) else str(value) if value is not None else ""
    return text[:max_length]


def _servicenow_timestamp(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _environment_non_negative_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(value, 0)


def _update_node_reached(
    state_manager: object,
    execution_identifier: str,
    node_name: str,
) -> None:
    db = getattr(state_manager, "db", None)
    if db is None:
        return

    try:
        models = import_module("src.db.models")
        execution = (
            db.query(models.Execution)
            .filter(models.Execution.execution_identifier == execution_identifier)
            .first()
        )
        if execution is None:
            return
        execution.node_reached = node_name
        db.commit()
    except Exception:
        db.rollback()
        raise

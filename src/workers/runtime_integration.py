"""Production composition for the published S2.2 and S2.5 worker APIs.

The imports are deliberately lazy: Sprint branches are developed separately,
but the final worker process imports the published S2.2/S2.5 modules once
those branches are present in its deployed source tree.  This module does not
copy persistence services or graph nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol


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
        execution = state_manager.create_execution(incident_reference=incident_number)
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
        del accepted_incident, result
        state_manager, close = self.state_manager_factory()
        try:
            state_manager.update_execution_status(
                self.context.execution_identifier,
                "succeeded",
            )
        finally:
            close()


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

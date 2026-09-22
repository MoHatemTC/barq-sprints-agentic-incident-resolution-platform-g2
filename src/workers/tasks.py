"""S2.3 Celery task orchestration — retry, DLQ, and integration seams."""

import os
from dataclasses import dataclass
from typing import Protocol

from celery import Celery, Task
from celery.exceptions import SoftTimeLimitExceeded

from src.agent.graph import compile_graph
from src.agent.checkpointer import get_checkpointer
from src.observability.tracing import trace_execution

from src.workers.celery_app import create_celery_app
from src.workers.dlq import DeadLetterEntry, create_dead_letter_entry
from src.workers.redis_consumer import MalformedIncidentPayload
from src.workers.retry_policy import RetryDecision, RetryPolicy
from src.workers.runtime_integration import (
    ExecutionContext,
    StateManagerTaskRecorder,
    context_from_task_headers,
)


class GraphAgentExecutor:
    """
    Implements the agent execution seam expected by the Celery worker infrastructure.
    Matches the AgentExecutor protocol.
    """
    
    @trace_execution(name="execute_incident_graph")
    def execute(self, accepted_incident: dict, execution_id: str = None, incident_number: str = None) -> dict:
        """
        Execute the LangGraph state machine.
        """
        # If execution_id/incident_number are passed as args, trace_execution will pick them up
        # If not, try to extract them from accepted_incident
        exec_id = execution_id or accepted_incident.get("execution_id", "default_exec_id")
        inc_num = incident_number or accepted_incident.get("number") or accepted_incident.get("sys_id", "UNKNOWN_INC")
        
        checkpointer = get_checkpointer()
        graph = compile_graph(checkpointer=checkpointer)
        
        initial_state = {
            "execution_id": exec_id,
            "incident_number": inc_num,
            "incident_payload": accepted_incident
        }
        
        config = {"configurable": {"thread_id": exec_id}}
        
        # Run the graph
        result = graph.invoke(initial_state, config=config)
        return result


class AgentExecutor(Protocol):
    """Agent execution seam for the worker task boundary."""

    def execute(self, accepted_incident: object) -> object:
        """Process one accepted incident or raise an exception."""


class StateRecorder(Protocol):
    """Retry/failure state recording seam for the worker task."""

    def record_retry(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        """Observe a retry-state write."""

    def record_failure(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        """Observe a terminal or exhausted failure write."""


class DlqSink(Protocol):
    """Dead-letter transition seam for the worker task."""

    def transition(self, entry: DeadLetterEntry) -> None:
        """Observe a dead-letter transition."""


@dataclass(frozen=True)
class IntegrationSeams:
    """Replaceable worker dependencies for agent execution, state recording, and DLQ."""

    agent: AgentExecutor
    state_recorder: StateRecorder
    dlq: DlqSink
    execution_context: object | None = None


@dataclass(frozen=True)
class ProductionIntegrationSeams:
    """Resolve real S2.2/S2.5 dependencies from the internal task context."""

    dlq: DlqSink
    failing_node: str = "celery_worker"

    def for_task(self, task: Task) -> IntegrationSeams:
        context = context_from_task_headers(getattr(task.request, "headers", None))
        request_args = getattr(task.request, "args", ()) or ()
        accepted_incident = request_args[0] if request_args else None
        if context is None:
            if not isinstance(accepted_incident, MalformedIncidentPayload):
                raise ValueError("S2.2 execution context is required for a valid incident")
            return IntegrationSeams(
                agent=GraphAgentExecutor(),
                state_recorder=_NoExecutionStateRecorder(),
                dlq=self.dlq,
            )
        return IntegrationSeams(
            agent=_ContextualGraphAgentExecutor(context),
            state_recorder=StateManagerTaskRecorder(context, self.failing_node),
            dlq=self.dlq,
            execution_context=context,
        )


class _NoExecutionStateRecorder:
    """Malformed wire data has no valid S2.2 execution to persist against."""

    def record_retry(self, accepted_incident: object, retries_completed: int, error: BaseException) -> None:
        del accepted_incident, retries_completed, error

    def record_failure(self, accepted_incident: object, retries_completed: int, error: BaseException) -> None:
        del accepted_incident, retries_completed, error


@dataclass(frozen=True)
class _ContextualGraphAgentExecutor:
    context: ExecutionContext

    def execute(self, accepted_incident: object) -> object:
        if not isinstance(accepted_incident, dict):
            raise ValueError("accepted incident must be a JSON object")
        incident_number = accepted_incident.get("number")
        return GraphAgentExecutor().execute(
            accepted_incident,
            execution_id=self.context.execution_identifier,
            incident_number=incident_number if isinstance(incident_number, str) else None,
        )


class _RetryableTimeoutForPolicy:
    """TEST-ONLY/PENDING TEAM AGREEMENT timeout classification for this scaffold."""

    retryable = True


def register_process_accepted_incident_task(
    retry_policy: RetryPolicy,
    seams: IntegrationSeams | ProductionIntegrationSeams,
    app: Celery | None = None,
) -> Task:
    """Register the S2.3 task scaffold on the supplied or configured Celery app."""
    celery_app = app or create_celery_app()

    @celery_app.task(bind=True, shared=False)
    def process_accepted_incident(task: Task, accepted_incident: object) -> object:
        task_seams = seams.for_task(task) if isinstance(seams, ProductionIntegrationSeams) else seams

        def handle_failure(
            error: BaseException, policy_error: BaseException
        ) -> tuple[RetryDecision, object | None]:
            retries_completed = task.request.retries
            decision = retry_policy.decide(policy_error, retries_completed)

            if decision is RetryDecision.RETRY:
                task_seams.state_recorder.record_retry(
                    accepted_incident,
                    retries_completed,
                    error,
                )
                retry_options: dict[str, object] = {
                    "exc": error,
                    "countdown": retry_policy.delay_for_retry(retries_completed + 1),
                    "max_retries": retry_policy.max_retries,
                }
                if isinstance(seams, ProductionIntegrationSeams):
                    retry_options["headers"] = getattr(task.request, "headers", None)
                return (
                    decision,
                    task.retry(**retry_options),
                )

            task_seams.state_recorder.record_failure(
                accepted_incident,
                retries_completed,
                error,
            )
            dlq_payload = (
                accepted_incident.original_payload
                if isinstance(accepted_incident, MalformedIncidentPayload)
                else accepted_incident
            )
            task_seams.dlq.transition(
                create_dead_letter_entry(
                    payload=dlq_payload,
                    execution_context=task_seams.execution_context,
                    error=error,
                    retry_count=retries_completed,
                    task_name=task.name,
                    task_id=getattr(task.request, "id", None),
                )
            )
            return decision, None

        try:
            if isinstance(accepted_incident, MalformedIncidentPayload):
                raise accepted_incident.error
            result = task_seams.agent.execute(accepted_incident)
            record_success = getattr(task_seams.state_recorder, "record_success", None)
            if callable(record_success):
                record_success(accepted_incident, result)
            return result
        except SoftTimeLimitExceeded as error:
            decision, result = handle_failure(error, _RetryableTimeoutForPolicy())
            if decision is RetryDecision.RETRY:
                return result
            raise
        except Exception as error:
            decision, result = handle_failure(error, error)
            if decision is RetryDecision.RETRY:
                return result
            raise

    return process_accepted_incident


# ---------------------------------------------------------------------------
# Temporary standalone task for local use until S2.3 branch is fully merged
# ---------------------------------------------------------------------------
redis_url = os.environ.get("REDIS_URL")
if redis_url:
    app = Celery('tasks', broker=redis_url, backend=redis_url)

    @app.task(bind=True, name="execute_incident_graph")
    def execute_incident_graph(self, execution_id: str, incident_number: str, initial_payload: dict):
        """
        Execute the LangGraph state machine inside the Celery worker.
        Uses the Postgres checkpointer for state persistence.
        """
        executor = GraphAgentExecutor()
        return executor.execute(
            accepted_incident=initial_payload,
            execution_id=execution_id,
            incident_number=incident_number
        )
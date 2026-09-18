"""TEST-ONLY/PENDING TEAM AGREEMENT Celery orchestration scaffold."""

from dataclasses import dataclass
from typing import Protocol

from celery import Celery, Task
from celery.exceptions import SoftTimeLimitExceeded

from src.workers.celery_app import create_celery_app
from src.workers.retry_policy import RetryDecision, RetryPolicy


class PendingAgentExecutorForTests(Protocol):
    """TEST-ONLY/PENDING S2.5 AGREEMENT agent execution seam."""

    def execute(self, accepted_incident: object) -> object:
        """Process one accepted incident or raise an exception."""


class PendingStateRecorderForTests(Protocol):
    """TEST-ONLY/PENDING S2.2 AGREEMENT retry/failure recording seam."""

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


class PendingDlqForTests(Protocol):
    """TEST-ONLY/PENDING S2.1 AGREEMENT DLQ transition seam."""

    def transition(self, accepted_incident: object, error: BaseException) -> None:
        """Observe a dead-letter transition."""


@dataclass(frozen=True)
class PendingIntegrationSeamsForTests:
    """Replaceable TEST-ONLY/PENDING TEAM AGREEMENT worker dependencies."""

    agent: PendingAgentExecutorForTests
    state_recorder: PendingStateRecorderForTests
    dlq: PendingDlqForTests


class _RetryableTimeoutForPolicy:
    """TEST-ONLY/PENDING TEAM AGREEMENT timeout classification for this scaffold."""

    retryable = True


def register_process_accepted_incident_task(
    retry_policy: RetryPolicy,
    seams: PendingIntegrationSeamsForTests,
    app: Celery | None = None,
) -> Task:
    """Register the S2.3 task scaffold on the supplied or configured Celery app."""
    celery_app = app or create_celery_app()

    @celery_app.task(bind=True)
    def process_accepted_incident(task: Task, accepted_incident: object) -> object:
        def handle_failure(
            error: BaseException, policy_error: BaseException
        ) -> tuple[RetryDecision, object | None]:
            retries_completed = task.request.retries
            decision = retry_policy.decide(policy_error, retries_completed)

            if decision is RetryDecision.RETRY:
                seams.state_recorder.record_retry(
                    accepted_incident,
                    retries_completed,
                    error,
                )
                return (
                    decision,
                    task.retry(
                        exc=error,
                        countdown=retry_policy.delay_for_retry(retries_completed + 1),
                        max_retries=retry_policy.max_retries,
                    ),
                )

            seams.state_recorder.record_failure(
                accepted_incident,
                retries_completed,
                error,
            )
            seams.dlq.transition(accepted_incident, error)
            return decision, None

        try:
            return seams.agent.execute(accepted_incident)
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

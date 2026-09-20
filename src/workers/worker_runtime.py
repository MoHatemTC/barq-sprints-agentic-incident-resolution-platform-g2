"""Production assembly of the S2.3 Redis consumer and Celery worker task."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from celery import Task

from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app
from src.workers.dlq import RedisListDlq, RedisListPublisher
from src.workers.redis_consumer import (
    RedisListClient,
    consume_next_incident_for_celery,
    run_incident_consumer_for_celery,
)
from src.workers.retry_policy import RetryPolicy
from src.workers.runtime_integration import establish_execution_context
from src.workers.tasks import ProductionIntegrationSeams, register_process_accepted_incident_task


class RedisWorkerClient(RedisListClient, RedisListPublisher, Protocol):
    """Redis operations used by the integrated S2.3 worker."""


@dataclass(frozen=True)
class IntegratedWorker:
    """S2.3 production entry point using the published S2.1/S2.2/S2.5 APIs."""

    redis_client: RedisListClient
    process_task: Task

    def consume_once(self, *, block_timeout_seconds: int = 5) -> bool:
        return consume_next_incident_for_celery(
            self.redis_client,
            self.process_task,
            establish_execution_context,
            block_timeout_seconds=block_timeout_seconds,
        )

    def run(self, should_stop: Callable[[], bool], *, block_timeout_seconds: int = 5) -> None:
        run_incident_consumer_for_celery(
            self.redis_client,
            self.process_task,
            establish_execution_context,
            should_stop,
            block_timeout_seconds=block_timeout_seconds,
        )


def create_integrated_worker(
    redis_client: RedisWorkerClient,
    config: WorkerConfig | None = None,
) -> IntegratedWorker:
    """Build the one S2.3 execution path without a second Celery app."""
    worker_config = config or WorkerConfig.from_environment()
    celery_app = create_celery_app(worker_config)
    retry_policy = RetryPolicy(
        base_delay_seconds=worker_config.retry_base_delay_seconds,
        max_delay_seconds=worker_config.retry_max_delay_seconds,
        max_retries=worker_config.task_max_retries,
    )
    process_task = register_process_accepted_incident_task(
        retry_policy,
        ProductionIntegrationSeams(
            dlq=RedisListDlq(redis_client, worker_config.dlq_queue),
        ),
        app=celery_app,
    )
    return IntegratedWorker(redis_client, process_task)

"""Celery application factory for S2.3 worker infrastructure."""

from celery import Celery

from src.config import WorkerConfig


def create_celery_app(config: WorkerConfig | None = None) -> Celery:
    """Create a Celery app using validated S2.3 worker configuration."""
    worker_config = config or WorkerConfig.from_environment()
    app = Celery("barq_workers", broker=worker_config.broker_url)
    app.conf.update(
        worker_concurrency=worker_config.worker_concurrency,
        worker_prefetch_multiplier=worker_config.worker_prefetch_multiplier,
        task_acks_late=worker_config.task_acks_late,
        task_reject_on_worker_lost=worker_config.task_reject_on_worker_lost,
        task_soft_time_limit=worker_config.task_soft_time_limit_seconds,
        task_time_limit=worker_config.task_time_limit_seconds,
    )
    return app

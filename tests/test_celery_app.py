from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app


def _worker_environment(**overrides):
    environment = {
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "CELERY_MAIN_QUEUE": "incident-events",
        "CELERY_DLQ_QUEUE": "incident-events-dlq",
        "CELERY_WORKER_CONCURRENCY": "4",
        "CELERY_WORKER_PREFETCH_MULTIPLIER": "1",
        "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": "30",
        "CELERY_TASK_TIME_LIMIT_SECONDS": "45",
        "CELERY_TASK_MAX_RETRIES": "3",
        "CELERY_RETRY_BASE_DELAY_SECONDS": "5",
        "CELERY_RETRY_MAX_DELAY_SECONDS": "60",
        "CELERY_TASK_ACKS_LATE": "true",
        "CELERY_TASK_REJECT_ON_WORKER_LOST": "false",
        "CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS": "60",
    }
    environment.update(overrides)
    return environment


def test_celery_app_uses_environment_configured_concurrency(monkeypatch):
    for name, value in _worker_environment(CELERY_WORKER_CONCURRENCY="9").items():
        monkeypatch.setenv(name, value)

    app = create_celery_app()

    assert WorkerConfig.from_environment().worker_concurrency == 9
    assert app.conf.worker_concurrency == 9


def test_celery_app_uses_validated_worker_configuration():
    config = WorkerConfig(
        broker_url="redis://redis:6379/0",
        main_queue="incident-events",
        dlq_queue="incident-events-dlq",
        worker_concurrency=4,
        worker_prefetch_multiplier=1,
        task_soft_time_limit_seconds=30,
        task_time_limit_seconds=45,
        task_max_retries=3,
        retry_base_delay_seconds=5,
        retry_max_delay_seconds=60,
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_shutdown_timeout_seconds=60,
    )

    app = create_celery_app(config)

    assert app.main == "barq_workers"
    assert app.conf.broker_url == "redis://redis:6379/0"
    assert app.conf.worker_concurrency == 4
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is False
    assert app.conf.task_soft_time_limit == 30
    assert app.conf.task_time_limit == 45
    assert app.conf.worker_soft_shutdown_timeout == 60


def test_celery_app_preserves_acknowledgement_and_worker_loss_settings():
    config = WorkerConfig(
        broker_url="redis://redis:6379/0",
        main_queue="incident-events",
        dlq_queue="incident-events-dlq",
        worker_concurrency=4,
        worker_prefetch_multiplier=1,
        task_soft_time_limit_seconds=30,
        task_time_limit_seconds=45,
        task_max_retries=3,
        retry_base_delay_seconds=5,
        retry_max_delay_seconds=60,
        task_acks_late=False,
        task_reject_on_worker_lost=True,
        worker_shutdown_timeout_seconds=60,
    )

    app = create_celery_app(config)

    assert app.conf.task_acks_late is False
    assert app.conf.task_reject_on_worker_lost is True


def test_celery_app_preserves_saturation_safety_settings():
    config = WorkerConfig(
        broker_url="redis://redis:6379/0",
        main_queue="incident-events",
        dlq_queue="incident-events-dlq",
        worker_concurrency=7,
        worker_prefetch_multiplier=3,
        task_soft_time_limit_seconds=30,
        task_time_limit_seconds=45,
        task_max_retries=3,
        retry_base_delay_seconds=5,
        retry_max_delay_seconds=60,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_shutdown_timeout_seconds=60,
    )

    app = create_celery_app(config)

    assert app.conf.worker_concurrency == 7
    assert app.conf.worker_prefetch_multiplier == 3
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True

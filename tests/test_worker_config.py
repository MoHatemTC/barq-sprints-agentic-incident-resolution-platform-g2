import pytest

from src.config import CHUNKING, WorkerConfig


def _environment(**overrides):
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


def test_worker_config_parses_valid_environment():
    config = WorkerConfig.from_environment(_environment())

    assert config.broker_url == "redis://redis:6379/0"
    assert config.main_queue == "incident-events"
    assert config.dlq_queue == "incident-events-dlq"
    assert config.worker_concurrency == 4
    assert config.worker_prefetch_multiplier == 1
    assert config.task_soft_time_limit_seconds == 30
    assert config.task_time_limit_seconds == 45
    assert config.task_max_retries == 3
    assert config.retry_base_delay_seconds == 5
    assert config.retry_max_delay_seconds == 60
    assert config.task_acks_late is True
    assert config.task_reject_on_worker_lost is False
    assert config.worker_shutdown_timeout_seconds == 60


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CELERY_WORKER_CONCURRENCY", "0", "positive integer"),
        ("CELERY_WORKER_PREFETCH_MULTIPLIER", "-1", "non-negative integer"),
        ("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "0", "positive integer"),
        ("CELERY_TASK_MAX_RETRIES", "-1", "non-negative integer"),
        ("CELERY_TASK_ACKS_LATE", "yes", "true or false"),
    ],
)
def test_worker_config_rejects_invalid_values(name, value, message):
    with pytest.raises(ValueError, match=message):
        WorkerConfig.from_environment(_environment(**{name: value}))


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CELERY_TASK_TIME_LIMIT_SECONDS", "30", "must be greater"),
        ("CELERY_RETRY_MAX_DELAY_SECONDS", "4", "must be >="),
    ],
)
def test_worker_config_rejects_invalid_value_relationships(name, value, message):
    with pytest.raises(ValueError, match=message):
        WorkerConfig.from_environment(_environment(**{name: value}))


def test_worker_config_rejects_missing_required_value():
    environment = _environment()
    del environment["CELERY_BROKER_URL"]

    with pytest.raises(
        ValueError,
        match="Missing required environment variable: CELERY_BROKER_URL",
    ):
        WorkerConfig.from_environment(environment)


def test_worker_config_does_not_change_existing_global_configuration():
    assert CHUNKING.chunk_size == 500
    assert CHUNKING.chunk_overlap == 50

from src.workers.celery_app import create_celery_app
from src.config import WorkerConfig

def test_timeout_configuration_and_platform_limitations():
    """
    Celery task time limits rely on the POSIX SIGALRM signal, which is not
    supported on Windows. Therefore, a true in-process time.sleep() test
    will not raise SoftTimeLimitExceeded on Windows.
    
    This test serves as the strongest deterministic verification possible:
    1. It verifies the timeout configuration reaches the Celery app correctly.
    2. We rely on the existing test_task_retries_soft_timeout_with_policy_delay
       to prove the task gracefully handles the exception when Celery *does* raise it
       on a supported platform (Linux).
    """
    config = WorkerConfig(
        broker_url="redis://localhost:6379/0",
        main_queue="test-q",
        dlq_queue="test-dlq",
        worker_concurrency=1,
        worker_prefetch_multiplier=1,
        task_soft_time_limit_seconds=1,
        task_time_limit_seconds=2,
        task_max_retries=1,
        retry_base_delay_seconds=1,
        retry_max_delay_seconds=2,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_shutdown_timeout_seconds=10
    )
    app = create_celery_app(config)
    
    assert app.conf.task_soft_time_limit == 1
    assert app.conf.task_time_limit == 2

    # We do not fake a timeout here. The behavior of the task catching
    # SoftTimeLimitExceeded is covered in test_worker_tasks.py via seam injection.

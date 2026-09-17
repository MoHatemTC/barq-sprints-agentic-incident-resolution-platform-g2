import pytest

from src.servicenow import exceptions as servicenow_exceptions
from src.workers.retry_policy import RetryDecision, RetryPolicy


class RetryableError(Exception):
    retryable = True


class TerminalError(Exception):
    retryable = False


def test_first_retry_uses_base_delay():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.delay_for_retry(1) == 5


def test_retry_delay_grows_exponentially():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert [policy.delay_for_retry(number) for number in (1, 2, 3)] == [5, 10, 20]


def test_retry_delay_is_capped():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=12,
        max_retries=3,
    )

    assert policy.delay_for_retry(3) == 12


def test_retryable_failure_retries_before_limit():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(RetryableError(), retries_completed=2) is RetryDecision.RETRY
    assert policy.decide(
        servicenow_exceptions.ServiceNowServerError(503, "unavailable"),
        retries_completed=0,
    ) is RetryDecision.RETRY


def test_retryable_failure_is_exhausted_at_limit():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(RetryableError(), retries_completed=3) is RetryDecision.EXHAUSTED


def test_terminal_failure_does_not_retry():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.decide(TerminalError(), retries_completed=0) is RetryDecision.TERMINAL
    assert policy.decide(
        servicenow_exceptions.ServiceNowPermissionError(403, "forbidden"),
        retries_completed=0,
    ) is RetryDecision.TERMINAL


def test_delay_calculation_is_deterministic():
    policy = RetryPolicy(
        base_delay_seconds=7,
        max_delay_seconds=60,
        max_retries=3,
    )

    assert policy.delay_for_retry(3) == policy.delay_for_retry(3) == 28


def test_zero_retries_exhausts_retryable_failure_immediately():
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=0,
    )

    assert policy.decide(RetryableError(), retries_completed=0) is RetryDecision.EXHAUSTED


def test_zero_delay_is_supported():
    policy = RetryPolicy(
        base_delay_seconds=0,
        max_delay_seconds=0,
        max_retries=3,
    )

    assert policy.delay_for_retry(1) == 0
    assert policy.delay_for_retry(3) == 0


@pytest.mark.parametrize(
    ("base_delay_seconds", "max_delay_seconds", "max_retries"),
    [
        (-1, 1, 1),
        (1, -1, 1),
        (1, 1, -1),
        (2, 1, 1),
    ],
)
def test_policy_rejects_invalid_configuration(
    base_delay_seconds,
    max_delay_seconds,
    max_retries,
):
    with pytest.raises(ValueError):
        RetryPolicy(
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
            max_retries=max_retries,
        )


@pytest.mark.parametrize("retry_number", [0, -1])
def test_delay_rejects_non_positive_retry_number(retry_number):
    policy = RetryPolicy(
        base_delay_seconds=5,
        max_delay_seconds=60,
        max_retries=3,
    )

    with pytest.raises(ValueError):
        policy.delay_for_retry(retry_number)

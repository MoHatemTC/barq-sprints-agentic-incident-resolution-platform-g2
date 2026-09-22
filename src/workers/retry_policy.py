"""Pure retry decisions and exponential backoff for worker failures."""

from dataclasses import dataclass
from enum import Enum


class RetryDecision(str, Enum):
    """The worker action selected for a failed execution."""

    RETRY = "retry"
    TERMINAL = "terminal"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration-driven retry policy with deterministic delays.

    retries_completed counts retries already scheduled for an event.
    retry_number is one-based: the first retry has number 1.
    """

    base_delay_seconds: int
    max_delay_seconds: int
    max_retries: int

    def __post_init__(self) -> None:
        _require_non_negative_int("base_delay_seconds", self.base_delay_seconds)
        _require_non_negative_int("max_delay_seconds", self.max_delay_seconds)
        _require_non_negative_int("max_retries", self.max_retries)
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")

    def delay_for_retry(self, retry_number: int) -> int:
        """Return the capped delay for a one-based retry number."""
        _require_positive_int("retry_number", retry_number)
        return min(
            self.base_delay_seconds * (2 ** (retry_number - 1)),
            self.max_delay_seconds,
        )

    def decide(self, error: BaseException, retries_completed: int) -> RetryDecision:
        """Classify an error using its optional retryable attribute."""
        _require_non_negative_int("retries_completed", retries_completed)
        if not getattr(error, "retryable", False):
            return RetryDecision.TERMINAL
        if retries_completed >= self.max_retries:
            return RetryDecision.EXHAUSTED
        return RetryDecision.RETRY


def _require_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")

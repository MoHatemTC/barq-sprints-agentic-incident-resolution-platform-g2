"""TEST-ONLY/PENDING TEAM AGREEMENT DLQ replay seam for S2.3 tests."""

from collections.abc import Callable
from typing import TypeVar


Result = TypeVar("Result")


def replay_dlq_payload(payload: object, process: Callable[[object], Result]) -> Result:
    """Pass one opaque DLQ payload to the supplied processing callable.

    This TEST-ONLY/PENDING TEAM AGREEMENT seam does not define a production DLQ
    envelope, queue, routing, retry, or dead-letter behavior.
    """
    if payload is None:
        raise ValueError("payload must not be None")
    if not callable(process):
        raise TypeError("process must be callable")
    return process(payload)

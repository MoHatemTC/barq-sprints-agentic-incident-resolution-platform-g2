"""Narrow S2.3 adapter for the confirmed S2.2 StateManager facade.

The runtime composition must provide an execution reference after the teams
agree how it is created and propagated. This module never derives it from an
accepted incident payload and intentionally does not choose a retry-row
lifecycle.
"""

from dataclasses import dataclass
from typing import Protocol


class StateManagerForWorker(Protocol):
    """The currently unambiguous S2.2 failure-recording operation."""

    def record_failure(
        self,
        execution_reference: str,
        failing_node: str,
        error_class: str,
        message: str,
        retry_count: int = 0,
    ) -> object:
        """Persist terminal or exhausted failure context."""


class RetryPersistenceForWorker(Protocol):
    """PENDING S2.2 AGREEMENT retry persistence operation."""

    def record_retry(
        self,
        execution_reference: str,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        """Persist a retry without selecting an S2.2 row lifecycle."""


@dataclass(frozen=True)
class StateManagerRecorder:
    """Bind explicit worker execution context to injected persistence seams."""

    state_manager: StateManagerForWorker
    retry_persistence: RetryPersistenceForWorker
    execution_reference: str
    failing_node: str

    def record_retry(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        """Delegate retry persistence without deciding create versus update."""
        del accepted_incident
        self.retry_persistence.record_retry(
            self.execution_reference,
            retries_completed,
            error,
        )

    def record_failure(
        self,
        accepted_incident: object,
        retries_completed: int,
        error: BaseException,
    ) -> None:
        """Delegate S2.2 failure persistence using injected execution context."""
        del accepted_incident
        self.state_manager.record_failure(
            execution_reference=self.execution_reference,
            failing_node=self.failing_node,
            error_class=type(error).__name__,
            message=str(error),
            retry_count=retries_completed,
        )

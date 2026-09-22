from datetime import datetime

from sqlalchemy.orm import Session

from src.db.event_service import process_event
from src.db.execution_service import (
    create_execution,
    update_execution_status,
)
from src.db.workflow_service import (
    save_checkpoint,
    get_latest_checkpoint,
)
from src.db.failure_service import (
    record_failure,
    get_failures,
)
from src.db.approval_service import (
    create_approval,
    get_approvals,
)
from src.db.retry_service import (
    create_retry_state,
    get_retry_state,
    update_retry_state,
)


class StateManager:
    """
    Coordinates access to workflow persistence services.

    The workflow should interact with this class instead of
    directly accessing individual database services.
    """

    def __init__(self, db: Session):
        self.db = db

    def process_event(
        self,
        event_identifier: str,
        incident_sys_id: str,
        incident_number: str,
        event_type: str,
        contract_version: str,
    ):
        return process_event(
            self.db,
            event_identifier,
            incident_sys_id,
            incident_number,
            event_type,
            contract_version,
        )

    def create_execution(
        self,
        incident_reference: str,
        agent_version: str = None,
        model_name: str = None,
    ):
        return create_execution(
            self.db,
            incident_reference,
            agent_version,
            model_name,
        )

    def update_execution_status(
        self,
        execution_identifier: str,
        status: str,
    ):
        return update_execution_status(
            self.db,
            execution_identifier,
            status,
        )

    def save_checkpoint(
        self,
        execution_reference: str,
        node_name: str,
        checkpoint: str,
    ):
        return save_checkpoint(
            self.db,
            execution_reference,
            node_name,
            checkpoint,
        )

    def get_latest_checkpoint(
        self,
        execution_reference: str,
    ):
        return get_latest_checkpoint(
            self.db,
            execution_reference,
        )

    def record_failure(
        self,
        execution_reference: str,
        failing_node: str,
        error_class: str,
        message: str,
        retry_count: int = 0,
    ):
        return record_failure(
            self.db,
            execution_reference,
            failing_node,
            error_class,
            message,
            retry_count,
        )

    def get_failures(
        self,
        execution_reference: str,
    ):
        return get_failures(
            self.db,
            execution_reference,
        )

    def create_approval(
        self,
        execution_reference: str,
        evidence_presented: str,
        reviewer_decision: str,
        reviewer_identity: str,
    ):
        return create_approval(
            self.db,
            execution_reference,
            evidence_presented,
            reviewer_decision,
            reviewer_identity,
        )

    def get_approvals(
        self,
        execution_reference: str,
    ):
        return get_approvals(
            self.db,
            execution_reference,
        )

    def create_retry_state(
        self,
        execution_reference: str,
        attempt_count: int,
        last_error: str = None,
        next_attempt_time: datetime = None,
    ):
        return create_retry_state(
            self.db,
            execution_reference,
            attempt_count,
            last_error,
            next_attempt_time,
        )

    def get_retry_state(
        self,
        execution_reference: str,
    ):
        return get_retry_state(
            self.db,
            execution_reference,
        )

    def update_retry_state(
        self,
        retry_state_id: int,
        attempt_count: int,
        last_error: str = None,
        next_attempt_time: datetime = None,
    ):
        return update_retry_state(
            self.db,
            retry_state_id,
            attempt_count,
            last_error,
            next_attempt_time,
        )
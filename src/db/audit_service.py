from sqlalchemy.orm import Session

from src.db.models import (
    Approval,
    Execution,
    Failure,
    WorkflowState,
)


def get_execution_audit(
    db: Session,
    execution_identifier: str,
):
    """
    Return the complete audit trail for one execution.

    The result includes:
    - execution status and timing
    - workflow nodes in execution order
    - checkpoint/evidence data
    - failures
    - approval decisions
    - termination reason
    """

    execution = (
        db.query(Execution)
        .filter(
            Execution.execution_identifier == execution_identifier
        )
        .first()
    )

    if execution is None:
        return None

    workflow_states = (
        db.query(WorkflowState)
        .filter(
            WorkflowState.execution_reference
            == execution_identifier
        )
        .order_by(
            WorkflowState.created_at.asc(),
            WorkflowState.id.asc(),
        )
        .all()
    )

    failures = (
        db.query(Failure)
        .filter(
            Failure.execution_reference
            == execution_identifier
        )
        .order_by(Failure.id.asc())
        .all()
    )

    approvals = (
        db.query(Approval)
        .filter(
            Approval.execution_reference
            == execution_identifier
        )
        .order_by(
            Approval.decision_timestamp.asc(),
            Approval.id.asc(),
        )
        .all()
    )

    if execution.status == "succeeded":
        termination_reason = "Execution completed successfully"
    elif execution.status == "failed":
        termination_reason = (
            failures[-1].message
            if failures
            else "Execution failed"
        )
    elif execution.status == "blocked":
        termination_reason = "Execution blocked"
    elif execution.status == "abandoned":
        termination_reason = "Execution abandoned"
    elif execution.status == "awaiting_approval":
        termination_reason = "Paused for human approval"
    else:
        termination_reason = "Execution still in progress"

    return {
        "execution": {
            "execution_identifier": execution.execution_identifier,
            "incident_reference": execution.incident_reference,
            "status": execution.status,
            "started_at": execution.started_at,
            "ended_at": execution.ended_at,
            "termination_reason": termination_reason,
        },
        "nodes": [
            {
                "sequence": index,
                "node_name": state.node_name,
                "checkpoint": state.checkpoint,
                "created_at": state.created_at,
                "updated_at": state.updated_at,
            }
            for index, state in enumerate(
                workflow_states,
                start=1,
            )
        ],
        "failures": [
            {
                "failing_node": failure.failing_node,
                "error_class": failure.error_class,
                "message": failure.message,
                "retry_count": failure.retry_count,
            }
            for failure in failures
        ],
        "approvals": [
            {
                "evidence_presented": approval.evidence_presented,
                "reviewer_decision": approval.reviewer_decision,
                "decision_timestamp": approval.decision_timestamp,
                "reviewer_identity": approval.reviewer_identity,
            }
            for approval in approvals
        ],
    }
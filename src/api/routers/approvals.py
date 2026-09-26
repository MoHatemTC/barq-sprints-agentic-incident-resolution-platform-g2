from datetime import datetime, timezone
import json

from fastapi import APIRouter, HTTPException

from src.api.schemas import (
    ApprovalListResponse,
    ApprovalDecision,
    ApprovalDecisionResponse,
)
from src.db.database import SessionLocal
from src.db.approval_service import create_approval
from src.db.execution_service import get_execution, update_execution_status
from src.db.idempotency import check_and_create_idempotency_key
from src.workers.tasks import resume_incident_graph


router = APIRouter()

VALID_ACTIONS = {"approve", "reject"}


@router.get(
    "/api/v1/approvals",
    response_model=ApprovalListResponse,
)
async def list_approvals(
    page: int = 1,
    page_size: int = 20,
):
    return ApprovalListResponse(
        items=[],
        page=page,
        page_size=page_size,
        total=0,
    )


@router.post(
    "/api/v1/approvals/{approval_id}/decide",
    response_model=ApprovalDecisionResponse,
)
async def decide_approval(
    approval_id: str,
    decision: ApprovalDecision,
):
    if decision.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail="action must be 'approve' or 'reject'",
        )

    if decision.action == "approve":
        if not decision.human_solution or not decision.human_solution.strip():
            raise HTTPException(
                status_code=422,
                detail="human_solution is required when approving an escalation",
            )

    db = SessionLocal()

    try:
        # approval_id is the execution identifier / LangGraph thread ID.
        execution = get_execution(
            db,
            approval_id,
        )

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Execution '{approval_id}' not found",
            )

        # Prevent the same approval decision from being processed twice.
        first_decision = check_and_create_idempotency_key(
            db,
            f"approval:{approval_id}",
        )

        if not first_decision:
            raise HTTPException(
                status_code=409,
                detail="Approval decision has already been processed",
            )

        status = (
            "approved"
            if decision.action == "approve"
            else "rejected"
        )

        evidence = json.dumps(
            {
                "incident_payload": None,
                "rationale": decision.rationale,
            },
            ensure_ascii=False,
        )

        approval = create_approval(
            db=db,
            execution_reference=approval_id,
            evidence_presented=evidence,
            reviewer_decision=status,
            reviewer_identity=decision.reviewer,
            human_solution=decision.human_solution,
        )

        # The approval has been recorded. Resume the SAME execution.
        update_execution_status(
            db,
            approval_id,
            "started",
        )

        resume_payload = {
            "decision": decision.action,
            "reviewer": decision.reviewer,
            "comment": decision.rationale,
            "human_solution": decision.human_solution,
        }

        try:
            resume_incident_graph.delay(
                approval_id,
                resume_payload,
            )
        except AttributeError:
            # Supports the current plain-function implementation used
            # in local/unit-test environments.
            resume_incident_graph(
                approval_id,
                resume_payload,
            )

        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=status,
            reviewer=decision.reviewer,
            decided_at=approval.decision_timestamp,
        )

    finally:
        db.close()
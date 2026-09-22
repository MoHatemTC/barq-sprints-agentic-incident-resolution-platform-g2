from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from src.api.schemas import ApprovalListResponse, ApprovalDecision, ApprovalDecisionResponse

router = APIRouter()

VALID_ACTIONS = {"approve", "reject"}

@router.get("/api/v1/approvals", response_model=ApprovalListResponse)
async def list_approvals(page: int = 1, page_size: int = 20):
    # TODO: replace with real query once the HITL approvals table exists
    return ApprovalListResponse(items=[], page=page, page_size=page_size, total=0)

@router.post("/api/v1/approvals/{approval_id}/decide", response_model=ApprovalDecisionResponse)
async def decide_approval(approval_id: str, decision: ApprovalDecision):
    if decision.action not in VALID_ACTIONS:
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'")

    # TODO: replace with real update once the HITL approvals table exists
    status = "approved" if decision.action == "approve" else "rejected"
    return ApprovalDecisionResponse(
        approval_id=approval_id,
        status=status,
        reviewer=decision.reviewer,
        decided_at=datetime.now(timezone.utc),
    )
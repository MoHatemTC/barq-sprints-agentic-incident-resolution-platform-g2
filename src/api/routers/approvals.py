<<<<<<< HEAD
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.dependencies import get_sync_db
from src.api.schemas import (
    ApprovalListResponse,
    ApprovalDecision,
    ApprovalDecisionResponse,
)
from src.db.approval_service import create_approval
from src.db.execution_service import get_execution, update_execution_status
from src.db.idempotency import check_and_create_idempotency_key
from src.config import WorkerConfig
from src.workers.celery_app import create_celery_app


celery_app = create_celery_app(WorkerConfig.from_environment())

=======
"""S3.4 approvals: list paused executions, show brief + raw payload, and resume
the SAME checkpointed execution with the reviewer's decision
"""

import json
import os
from datetime import datetime, timezone
from functools import lru_cache

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from src.agent.checkpointer import get_run_state, is_paused
from src.db.approval_service import create_approval, get_approvals
from src.db.database import SessionLocal
from src.db.execution_service import update_execution_status
from src.db.idempotency import check_and_create_idempotency_key
from src.db.models import Execution
from src.api.schemas import (
    ApprovalBrief,
    ApprovalDecision,
    ApprovalDecisionResponse,
    ApprovalDetail,
    ApprovalListResponse,
    ApprovalResponse,
)
>>>>>>> origin/development

router = APIRouter()

VALID_ACTIONS = {"approve", "reject"}
# Must match src.workers.tasks.RESUME_TASK_NAME (not imported: that module builds Celery apps)
RESUME_TASK_NAME = "resume_incident_graph"


class SqlApprovalStore:
    """Postgres side of approvals, on the sync S2.2 services."""

    def awaiting_execution_ids(self) -> list[str]:
        with SessionLocal() as db:
            rows = (
                db.query(Execution.execution_identifier)
                .filter(Execution.status == "awaiting_approval")
                .order_by(Execution.started_at.desc())
                .all()
            )
        return [row[0] for row in rows]

    def claim_decision(self, execution_id: str) -> bool:
        """First decision wins; a second (double click, second reviewer) gets False."""
        with SessionLocal() as db:
            return check_and_create_idempotency_key(db, f"approval:{execution_id}")

    def record_decision(self, execution_id: str, evidence: str, decision: str, reviewer: str) -> None:
        with SessionLocal() as db:
            create_approval(db, execution_id, evidence, decision, reviewer)
            # Resumed and running again until the worker records the final outcome
            update_execution_status(db, execution_id, "started")

    def recorded_decision(self, execution_id: str) -> dict | None:
        """The decision already stored for this execution, used to re-send a lost resume."""
        with SessionLocal() as db:
            rows = get_approvals(db, execution_id)
            if not rows:
                return None
            row = rows[-1]
            try:
                rationale = json.loads(row.evidence_presented).get("rationale")
            except (TypeError, ValueError, AttributeError):
                rationale = None
            return {
                "status": row.reviewer_decision,
                "reviewer": row.reviewer_identity,
                "rationale": rationale,
                "decided_at": row.decision_timestamp,
            }


@lru_cache
def _celery() -> Celery:
    return Celery("barq_api", broker=os.environ["CELERY_BROKER_URL"])


def dispatch_resume(execution_id: str, decision: dict) -> None:
    """Hand the resume to the worker, which has retries and crash recovery."""
    _celery().send_task(RESUME_TASK_NAME, args=[execution_id, decision])


def get_approval_store():
    return SqlApprovalStore()


@lru_cache
def _compiled_graph():
    from src.agent.checkpointer import get_checkpointer
    from src.agent.graph import compile_graph
    return compile_graph(checkpointer=get_checkpointer())


def warm_up() -> None:
    try:
        _compiled_graph()
    except Exception:
        pass  # requests retry and return 503 while the store is unavailable


def get_approval_graph():
    # lru_cache does not cache exceptions, so a later request retries the connection
    try:
        return _compiled_graph()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Checkpoint store unavailable: {type(exc).__name__}"
        ) from exc


def get_resume_dispatcher():
    return dispatch_resume


def _brief(values: dict):
    brief = values.get("approval_brief")
    if not brief:
        return None
    try:
        return ApprovalBrief(**brief)
    except (TypeError, ValidationError):
        return None


def _summary(execution_id: str, values: dict) -> dict:
    payload = values.get("interrupt_payload") or {}
    return {
        "approval_id": execution_id,
        "execution_id": execution_id,
        "incident_number": values.get("incident_number"),
        "incident_sys_id": (payload.get("incident") or {}).get("sys_id"),
        "status": "pending",
        "gate": values.get("gate") or payload.get("gate"),
        "reason_text": payload.get("reason_text"),
        "brief_status": "generated" if _brief(values) else "unavailable",
        "created_at": payload.get("created_at"),
    }


def _paused_values(graph, execution_id: str) -> dict:
    snapshot = get_run_state(graph, execution_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No execution {execution_id}")
    if not is_paused(snapshot):
        raise HTTPException(
            status_code=409,
            detail="Execution is not awaiting approval (already decided or never paused)",
        )
    return snapshot.values


<<<<<<< HEAD

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
    db: Session = Depends(get_sync_db),
=======
@router.get("/api/v1/approvals", response_model=ApprovalListResponse)
def list_approvals(
    page: int = 1,
    page_size: int = 20,
    store=Depends(get_approval_store),
    graph=Depends(get_approval_graph),
):
    items = []
    for execution_id in store.awaiting_execution_ids():
        snapshot = get_run_state(graph, execution_id)
        if is_paused(snapshot):
            items.append(ApprovalResponse(**_summary(execution_id, snapshot.values)))
    start = (max(page, 1) - 1) * page_size
    return ApprovalListResponse(
        items=items[start:start + page_size], page=page, page_size=page_size, total=len(items)
    )


@router.get("/api/v1/approvals/{approval_id}", response_model=ApprovalDetail)
def get_approval(approval_id: str, graph=Depends(get_approval_graph)):
    values = _paused_values(graph, approval_id)
    return ApprovalDetail(
        **_summary(approval_id, values),
        brief=_brief(values),
        payload=values.get("interrupt_payload") or {},
    )


def _send_resume(dispatch, execution_id: str, action: str, reviewer: str, comment) -> None:
    try:
        dispatch(execution_id, {"decision": action, "reviewer": reviewer, "comment": comment})
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Decision recorded but the resume could not be queued, retry the same request: {exc}",
        ) from exc


@router.post("/api/v1/approvals/{approval_id}/decide", response_model=ApprovalDecisionResponse)
def decide_approval(
    approval_id: str,
    decision: ApprovalDecision,
    store=Depends(get_approval_store),
    graph=Depends(get_approval_graph),
    dispatch=Depends(get_resume_dispatcher),
>>>>>>> origin/development
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

    # Queue the resume operation on the production Celery worker.
    # The HTTP request never executes the LangGraph/LLM/
    # ServiceNow/Qdrant chain synchronously.
    celery_app.send_task(
        "resume_incident_graph",
        args=[approval_id, resume_payload],
    )

<<<<<<< HEAD
=======
    values = _paused_values(graph, approval_id)
    status = "approved" if decision.action == "approve" else "rejected"

    if not store.claim_decision(approval_id):
        recorded = store.recorded_decision(approval_id)
        if not recorded or (recorded["status"], recorded["reviewer"]) != (status, decision.reviewer):
            raise HTTPException(status_code=409, detail="A decision was already recorded for this execution")
        _send_resume(dispatch, approval_id, decision.action, recorded["reviewer"], recorded["rationale"])
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=recorded["status"],
            reviewer=recorded["reviewer"],
            decided_at=recorded["decided_at"],
            resumed=True,
        )
    # What the reviewer saw, persisted before the resume (NFR-07 audit)
    evidence = json.dumps(
        {
            "payload": values.get("interrupt_payload"),
            "brief": values.get("approval_brief"),
            "rationale": decision.rationale,
        },
        default=str,
    )
    store.record_decision(approval_id, evidence, status, decision.reviewer)
    _send_resume(dispatch, approval_id, decision.action, decision.reviewer, decision.rationale)

>>>>>>> origin/development
    return ApprovalDecisionResponse(
        approval_id=approval_id,
        status=status,
        reviewer=decision.reviewer,
<<<<<<< HEAD
        decided_at=approval.decision_timestamp,
    )
=======
        decided_at=datetime.now(timezone.utc),
        resumed=True,
    )
>>>>>>> origin/development

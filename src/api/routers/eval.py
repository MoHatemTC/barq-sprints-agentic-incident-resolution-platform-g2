import uuid
from datetime import datetime, timezone
from fastapi import APIRouter
from src.api.schemas import EvalRunRequest, EvalRunResponse, EvalResultsListResponse

router = APIRouter()

@router.get("/api/v1/eval/results", response_model=EvalResultsListResponse)
async def get_eval_results(page: int = 1, page_size: int = 20):
    # TODO: replace with real query once eval infra exists (Sprint 4, per Sarah)
    return EvalResultsListResponse(items=[], page=page, page_size=page_size, total=0)

@router.post("/api/v1/eval/run", response_model=EvalRunResponse)
async def trigger_eval_run(request: EvalRunRequest):
    # TODO: replace with real Celery-triggered eval run once eval infra exists (Sprint 4, per Sarah)
    return EvalRunResponse(
        run_id=str(uuid.uuid4()),
        status="queued",
        created_at=datetime.now(timezone.utc),
    )
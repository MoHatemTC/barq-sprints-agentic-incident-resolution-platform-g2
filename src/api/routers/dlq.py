from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from src.api.schemas import DLQListResponse, DLQReplayResponse
from src.api.auth import require_operator_role

router = APIRouter()

@router.get("/api/v1/dlq", response_model=DLQListResponse)
async def list_dlq(page: int = 1, page_size: int = 20):
    # TODO: replace with real query once S2.3 DLQ (Abdullah) lands
    return DLQListResponse(items=[], page=page, page_size=page_size, total=0)

@router.post("/api/v1/dlq/{event_id}/replay", response_model=DLQReplayResponse)
async def replay_dlq_event(event_id: str, _=Depends(require_operator_role)):
    # TODO: replace with real replay logic (re-enqueue to Redis) once S2.3 DLQ lands
    return DLQReplayResponse(event_id=event_id, status="requeued", requeued_at=datetime.now(timezone.utc))
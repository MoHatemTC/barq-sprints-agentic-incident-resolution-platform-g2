from datetime import datetime, timezone
from fastapi import APIRouter
from src.api.schemas import ExecutionResponse, ExecutionTraceResponse, TraceNode, PaginatedExecutionsResponse

router = APIRouter()

@router.get("/api/v1/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str):
    # TODO: replace with real query once Mostafa's S2.2 execution table exists
    return ExecutionResponse(
        execution_id=execution_id,
        incident_sys_id="stub-sys-id",
        status="started",
        current_node="classify",
        started_at=datetime.now(timezone.utc),
        model="gemini-1.5-pro",
        agent_version="v0-stub",
    )

@router.get("/api/v1/executions/{execution_id}/trace", response_model=ExecutionTraceResponse)
async def get_execution_trace(execution_id: str):
    # TODO: replace with real Langfuse-backed trace once S2.5 lands
    return ExecutionTraceResponse(
        execution_id=execution_id,
        nodes=[TraceNode(node_name="classify", entered_at=datetime.now(timezone.utc), output="stub")],
    )

@router.get("/api/v1/incidents/{sys_id}/executions", response_model=PaginatedExecutionsResponse)
async def list_executions_for_incident(sys_id: str, page: int = 1, page_size: int = 20):
    # TODO: replace with real paginated query once S2.2 execution table exists
    return PaginatedExecutionsResponse(items=[], page=page, page_size=page_size, total=0)
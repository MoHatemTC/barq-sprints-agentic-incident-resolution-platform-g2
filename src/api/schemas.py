from pydantic import BaseModel, Field, computed_field
from pydantic_settings import BaseSettings
from typing import Optional
from datetime import datetime
from enum import Enum

#settings pydantic model to validate the environment variables
class Settings(BaseSettings):
    postgres_host: str = Field(default="localhost", description="The hostname of the PostgreSQL database")
    postgres_port: int = Field(default=5432, description="The port number of the PostgreSQL database")
    postgres_user: str = Field(default="app", description="The username for the PostgreSQL database")
    postgres_password: str = Field(default="app", description="The password for the PostgreSQL database")
    postgres_db: str = Field(default="orchestrator", description="The name of the PostgreSQL database")
    redis_host: str = Field(default="localhost", description="The hostname of the Redis server")
    redis_port: int = Field(default=6379, description="The port number of the Redis server")
    redis_db: int = Field(default=0, description="The database number of the Redis server")
    redis_password: Optional[str] = Field(default=None, description="The password for the Redis server, if any")
    webhook_auth_token: str = Field(..., description="Shared bearer token ServiceNow must send")
    langfuse_public_key: str = Field(..., description="Langfuse public key")
    langfuse_secret_key: str = Field(..., description="Langfuse secret key")
    langfuse_base_url: str = Field(default="https://cloud.langfuse.com", description="Langfuse host URL")

    @computed_field
    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @computed_field
    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        else:
            return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

#payload pydantic model to validate the incoming webhook payload
class Payload(BaseModel):

    event_id: str = Field(..., description="The unique identifier for the webhook event")
    sys_id: str = Field(..., description="The unique identifier for the incident")
    number: str = Field(..., description="The incident number")
    event_type: str = Field(..., description="The type of event that triggered the webhook")
    contract_version: str = Field(..., description="The version of the contract that defines the webhook payload structure")

class ExecutionResponse(BaseModel):
    execution_id: str
    incident_sys_id: str
    status: str  # started | succeeded | failed | blocked | awaiting_approval
    current_node: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    model: str
    agent_version: str

class TraceNode(BaseModel):
    node_name: str
    entered_at: datetime
    output: Optional[str] = None

class ExecutionTraceResponse(BaseModel):
    execution_id: str
    nodes: list[TraceNode]

class PaginatedExecutionsResponse(BaseModel):
    items: list[ExecutionResponse]
    page: int
    page_size: int
    total: int

class ApprovalResponse(BaseModel):
    approval_id: str
    incident_sys_id: str
    status: str  # pending | approved | rejected
    created_at: datetime

class ApprovalDecision(BaseModel):
    action: str  # "approve" or "reject"
    reviewer: str
    rationale: Optional[str] = None

class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    status: str
    reviewer: str
    decided_at: datetime

class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    page: int
    page_size: int
    total: int

class DLQEntryResponse(BaseModel):
    event_id: str
    original_payload: dict
    failure_reason: str
    retry_count: int
    timestamp: datetime

class DLQListResponse(BaseModel):
    items: list[DLQEntryResponse]
    page: int
    page_size: int
    total: int

class DLQReplayResponse(BaseModel):
    event_id: str
    status: str
    requeued_at: datetime

class EvalRunRequest(BaseModel):
    dataset_id: str
    agent_version: Optional[str] = None
    model: Optional[str] = None
    parameters: Optional[dict] = None

class EvalRunResponse(BaseModel):
    run_id: str
    status: str  # queued | running | completed | failed
    created_at: datetime

class EvalResultResponse(BaseModel):
    run_id: str
    status: str
    metrics: dict  # e.g. {"resolution_rate": 0.0, "accuracy": 0.0, "avg_latency_ms": 0.0}
    total_samples: int
    completed_at: Optional[datetime] = None

class EvalResultsListResponse(BaseModel):
    items: list[EvalResultResponse]
    page: int
    page_size: int
    total: int
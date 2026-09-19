# Sprint 2 — API Surface (S2.1)

## Purpose

This document is the complete, locked contract for every HTTP endpoint
exposed by the S2.1 FastAPI application: paths, methods, request/response
schemas, authentication requirements, and status codes. Endpoints marked
**stub** return correctly-shaped placeholder data; the route, schema, and
status codes are locked so downstream sprints can integrate against them
without route restructuring, per the S2.1 brief.

## Authentication summary

Two independent auth mechanisms are used, chosen deliberately for different
callers:

| Mechanism | Used by | Header | Failure modes |
|---|---|---|---|
| `verify_token` | ServiceNow (webhook only) | `Authorization: Bearer <shared secret>` | 401 on missing/invalid token |
| `require_operator_role` | Internal operator actions | `Authorization: Bearer <JWT>` | 401 on invalid/missing JWT, 403 if `role` claim ≠ `operator` |

Endpoints with no auth dependency listed below are unauthenticated.

## Error taxonomy

Every error response, regardless of status code, uses one shape:

```json
{
  "error": {
    "code": 401,
    "message": "Unauthorized",
    "correlation_id": "f8df21e3-9b44-4cdc-b80c-8a67f9946014"
  }
}
```

`code` mirrors the HTTP status code. `correlation_id` matches the
`X-Correlation-ID` response header, generated fresh per request or echoed
back if the caller supplied one, letting any error be traced to its
structured log line and Langfuse span.

## Route table

### Health

| Method | Path | Auth | Status codes | Notes |
|---|---|---|---|---|
| GET | `/health` | none | 200 | Liveness only — no dependency checks |
| GET | `/ready` | none | 200, 503 | Readiness — pings Redis and runs `SELECT 1` against Postgres |

**`/health` response**: `200 OK`, body `"OK"`.

**`/ready` response**: `200 OK`, body `"Ready"` if both checks pass; `503`
with `{"error": {...}}` naming which dependency failed if either check
fails.

### Ingestion webhook

| Method | Path | Auth | Status codes |
|---|---|---|---|
| POST | `/api/v1/webhook/incident` | `verify_token` | 202, 401, 422 |

**Request — `Payload`**
```json
{
  "event_id": "string",
  "sys_id": "string",
  "number": "string",
  "event_type": "string",
  "contract_version": "string"
}
```
All fields required. `contract_version` must be one of
`SUPPORTED_CONTRACT_VERSIONS` (currently `{"v1"}`) or the request is
rejected with 422 before any persistence occurs.

**Behavior**: validates auth → validates schema/contract version → persists
to Postgres (`Event` row, `event_id` unique) → enqueues the payload as JSON
onto the Redis list `incident_events` via `RPUSH` → returns 202. A duplicate
`event_id` (unique constraint violation) short-circuits after the failed
insert: the transaction rolls back and 202 is returned without a Redis
enqueue, since the event was already durably recorded on first delivery.

**Response**: `202 Accepted`, plain text body confirming receipt (or
duplicate).

### System configuration

| Method | Path | Auth | Status codes |
|---|---|---|---|
| GET | `/api/v1/config` | none | 200 |

**Response — `ConfigResponse`** (redacted — secrets never included)
```json
{
  "postgres_host": "string",
  "postgres_port": 0,
  "redis_host": "string",
  "redis_port": 0
}
```

### Execution audit — **stub**

Schemas align to the internal backend execution-tracking model (S2.2), not
the ServiceNow-side `x_2215689_ai_inc_0_ai_execution_log` table, per
confirmed design direction.

| Method | Path | Auth | Status codes |
|---|---|---|---|
| GET | `/api/v1/executions/{execution_id}` | none | 200 |
| GET | `/api/v1/executions/{execution_id}/trace` | none | 200 |
| GET | `/api/v1/incidents/{sys_id}/executions` | none | 200 |

**`ExecutionResponse`**
```json
{
  "execution_id": "string",
  "incident_sys_id": "string",
  "status": "started | succeeded | failed | blocked | awaiting_approval",
  "current_node": "string | null",
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:00Z | null",
  "model": "string",
  "agent_version": "string"
}
```

**`ExecutionTraceResponse`**
```json
{
  "execution_id": "string",
  "nodes": [
    {"node_name": "string", "entered_at": "2026-01-01T00:00:00Z", "output": "string | null"}
  ]
}
```

**`PaginatedExecutionsResponse`** (list endpoint, query params `page`,
`page_size`)
```json
{
  "items": [ /* ExecutionResponse */ ],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

### HITL approvals — **stub**

Modeled as a distinct entity with its own review lifecycle, separate from
the execution log, per confirmed design direction.

| Method | Path | Auth | Status codes |
|---|---|---|---|
| GET | `/api/v1/approvals` | none | 200 |
| POST | `/api/v1/approvals/{approval_id}/decide` | none | 200, 422 |

**`ApprovalListResponse`**
```json
{
  "items": [
    {"approval_id": "string", "incident_sys_id": "string", "status": "pending | approved | rejected", "created_at": "2026-01-01T00:00:00Z"}
  ],
  "page": 1, "page_size": 20, "total": 0
}
```

**Request — `ApprovalDecision`**
```json
{
  "action": "approve | reject",
  "reviewer": "string",
  "rationale": "string | null"
}
```
`action` outside `{"approve", "reject"}` returns 422.

**`ApprovalDecisionResponse`**
```json
{
  "approval_id": "string",
  "status": "approved | rejected",
  "reviewer": "string",
  "decided_at": "2026-01-01T00:00:00Z"
}
```

### Dead-letter queue — **stub**

| Method | Path | Auth | Status codes |
|---|---|---|---|
| GET | `/api/v1/dlq` | none | 200 |
| POST | `/api/v1/dlq/{event_id}/replay` | `require_operator_role` | 200, 401, 403 |

**`DLQListResponse`**
```json
{
  "items": [
    {"event_id": "string", "original_payload": {}, "failure_reason": "string", "retry_count": 0, "timestamp": "2026-01-01T00:00:00Z"}
  ],
  "page": 1, "page_size": 20, "total": 0
}
```

**`DLQReplayResponse`**
```json
{
  "event_id": "string",
  "status": "requeued",
  "requeued_at": "2026-01-01T00:00:00Z"
}
```

### Evaluation — **stub**

Fields confirmed against the platform's actual eval requirements (job
orchestration + aggregate metrics), not invented.

| Method | Path | Auth | Status codes |
|---|---|---|---|
| GET | `/api/v1/eval/results` | none | 200 |
| POST | `/api/v1/eval/run` | none | 200 |

**Request — `EvalRunRequest`**
```json
{
  "dataset_id": "string",
  "agent_version": "string | null",
  "model": "string | null",
  "parameters": {} 
}
```

**`EvalRunResponse`**
```json
{
  "run_id": "string",
  "status": "queued",
  "created_at": "2026-01-01T00:00:00Z"
}
```

**`EvalResultsListResponse`**
```json
{
  "items": [
    {
      "run_id": "string",
      "status": "string",
      "metrics": {"resolution_rate": 0.0, "accuracy": 0.0, "avg_latency_ms": 0.0},
      "total_samples": 0,
      "completed_at": "2026-01-01T00:00:00Z | null"
    }
  ],
  "page": 1, "page_size": 20, "total": 0
}
```

## Downstream integration notes

Every stub endpoint's TODO in source points at the specific sprint
deliverable it depends on:

- Execution audit → S2.2 execution table
- HITL approvals → the HITL approvals table (likely tied to S2.5)
- DLQ → S2.3 Celery/DLQ infrastructure
- Eval → eval infrastructure, targeted for Sprint 4 per mentor guidance

None of these require route, schema, or status-code changes once their
backing systems land — only the placeholder logic inside each handler is
replaced.

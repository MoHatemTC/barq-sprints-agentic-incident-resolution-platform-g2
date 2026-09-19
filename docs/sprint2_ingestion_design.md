# Sprint 2 — Ingestion Design (S2.1)

## Purpose

This document describes the architecture of the FastAPI ingestion webhook and
supporting application shell built for Sprint 2 Task S2.1, the design
decisions behind it, and the evidence required by FR-05 (no polling against
ServiceNow).

## Architecture overview

The application follows a strict **202-accepted, fire-and-forget** pattern
for incoming ServiceNow events:

```
ServiceNow Business Rule
        |
        v
POST /webhook  (FastAPI)
        |
        1. verify_token()      — reject 401 on missing/invalid Bearer token
        2. validate Payload    — reject 422 on schema or unsupported
                                  contract_version
        3. persist to Postgres — durable record, before anything else
        4. enqueue to Redis    — rpush onto `incident_events` list
        5. return 202          — caller does not wait for AI processing
        |
        v
Redis list `incident_events`
        |
        v
Celery workers (S2.3, downstream)
        |
        v
LangGraph state machine (S2.5, downstream) → ServiceNow (S1.5 client)
```

The webhook itself never invokes retrieval, model inference, or any
ServiceNow write. Its only responsibilities are authentication, schema
validation, durable persistence, and queueing — everything downstream is the
responsibility of the Celery workers built in later sprints.

## Application shell

- **`create_app()`** factory in `src/api/app.py` builds the FastAPI instance,
  registers middleware, and wires all routers. A single `lifespan` context
  manager creates the Postgres connection pool (`create_async_engine`) and
  the Redis client once at startup, attaches them to `app.state`, and
  disposes/closes them on shutdown.
- **Dependency injection** (`src/api/dependencies.py`) exposes
  `get_settings`, `get_redis`, and `get_db_session` as `Depends(...)`
  providers rather than direct imports, so tests can swap in mocks via
  `app.dependency_overrides` without touching real infrastructure.
- **Auth** (`src/api/auth.py`) is deliberately split into two independent
  paths, per a design decision confirmed with the mentor:
  - `verify_token` — a static shared Bearer secret for the ServiceNow →
    webhook machine-to-machine call, chosen for low latency and simplicity
    on the ingestion hot path.
  - `decode_bearer_token` / `require_operator_role` — a JWT-based check
    carrying a `role` claim, used only for internal operator actions (e.g.
    DLQ replay), returning 401 for an invalid/missing token and 403 for a
    valid token lacking the operator role.
- **Middleware**: a correlation-ID middleware (`X-Correlation-ID`) tags every
  request, feeding both structured JSON logging and Langfuse tracing spans so
  a request can be traced end-to-end across logs and traces by one ID.
- **Error taxonomy**: global exception handlers wrap every error — 401, 422,
  503, and unhandled 500s — into one consistent JSON shape:
  `{"error": {"code", "message", "correlation_id"}}`.

## Idempotency

Duplicate webhook deliveries (ServiceNow retries) are handled at the
database layer: the `event_id` column carries a `unique` constraint. On a
duplicate insert, SQLAlchemy raises `IntegrityError`; the webhook catches
this, rolls back, and returns 202 without enqueueing to Redis a second time.
This avoids a check-then-insert race condition that a manual
"query-then-insert" approach would be vulnerable to under concurrent
retries.

## Performance

Sustained load testing (100 concurrent users, 5 minutes, ~92,000 requests)
against the containerized application measured:

| Metric | Value |
|---|---|
| p50 | 14ms |
| p95 | 51ms |
| p99 | 110ms |
| Failures | 0 |

This is well within the NFR-01 500ms p95 budget. A queue-depth independence
test confirmed p95 latency stays flat (51–55ms) whether the Redis queue is
empty or holds 50,000 pending items, consistent with `rpush` being an O(1)
operation regardless of list length.

Reaching this result required fixing three real bottlenecks found during
testing, documented here since they inform future performance work:

1. **Langfuse tracing was calling `flush()` on every request**, which
   forces a synchronous network round-trip to Langfuse's servers before the
   response can return. Fixed by removing the per-request flush and calling
   it once on application shutdown instead — tracing events are meant to be
   buffered and sent asynchronously in the background.
2. **The SQLAlchemy engine had no explicit connection pool size**, defaulting
   to 5 base connections + 10 overflow (15 total). Under 100 concurrent
   webhook calls, most requests queued waiting for a free connection. Fixed
   by setting `pool_size=20, max_overflow=20` on `create_async_engine`.
3. **The Dockerfile's `uvicorn` command ran a single worker process**,
   serializing all request handling through one event loop. Fixed by adding
   `--workers 4`, allowing concurrent requests to be handled by separate
   processes.

## FR-05 — No polling against ServiceNow

FR-05 requires that this service never polls ServiceNow. The ingestion
webhook is purely reactive — it exposes an endpoint ServiceNow calls, and
never itself makes an outbound call to ServiceNow on a timer, loop, or
interval.

Code search evidence (run from the project root):

```bash
$ grep -rn "while True" src/ | grep -i servicenow
(no results)

$ grep -rn "sleep\|interval\|schedule" src/api/
(no results)
```

No scheduling, polling loop, or interval-based call to ServiceNow exists
anywhere in the `src/api/` application. The only outbound calls to
ServiceNow in this codebase belong to the S1.5 `ServiceNowClient`
(`get_incident`, `update_incident`, `add_work_note`, `write_execution_log`),
each of which is invoked on demand by a Celery worker processing a specific
queued event — a one-time, event-triggered fetch, not a recurring poll.

## Secrets handling in logs

Structured logging (`src/api/middleware.py`) intentionally logs only
`method`, `path`, `status_code`, `duration_ms`, and `correlation_id` for
every request — never the request body, headers, or the application
`Settings` object, which would risk leaking `webhook_auth_token`,
`postgres_password`, or `redis_password`.

Code search evidence:

```bash
$ grep -rn "postgres_password\|webhook_auth_token\|redis_password" src/ | grep -i log
(no results)
```

The `/api/v1/config` endpoint independently enforces this by returning a
dedicated `ConfigResponse` schema that only exposes non-secret fields
(`postgres_host`, `postgres_port`, `redis_host`, `redis_port`), rather than
serializing the `Settings` object directly.

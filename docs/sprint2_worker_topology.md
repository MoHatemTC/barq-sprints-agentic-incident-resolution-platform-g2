# Sprint 2.3 — Worker Topology and Runtime Architecture

## Ownership

S2.3 owns Redis queue consumption, Celery worker configuration, task
orchestration, retry classification and bounded exponential backoff, dead-letter
handling, execution-context propagation, and the runtime composition layer that
bridges S2.2 (state) and S2.5 (agent graph).

**S2.3 does NOT own:**

- S2.1 webhook implementation or producer-side Redis enqueue
- S2.1 HTTP DLQ replay endpoint
- S2.2 schema, migrations, or idempotency implementation
- S2.4 retrieval or reranking
- S2.5 agent-node or domain behavior implementation
- Production deployment configuration values

| Area | Owner | S2.3 role |
|---|---|---|
| Webhook acceptance, validation, Redis enqueue | S2.1 | Consumer only. S2.3 reads from the list S2.1 writes to. |
| PostgreSQL execution/retry/failure state, schema, migrations | S2.2 | Caller only. S2.3 calls the published `StateManager` API. |
| Redis queue topology, Celery workers, retry, backoff, DLQ | S2.3 | Owner. |
| Retrieval and reranking | S2.4 | None. |
| Agent graph, LangGraph nodes, state, checkpointing, tracing | S2.5 | Invoker only. S2.3 calls the published graph entry point. |

## Production modules

```text
src/workers/
├── celery_app.py           Celery application factory
├── retry_policy.py         RetryDecision enum, RetryPolicy with bounded backoff
├── dlq.py                  DeadLetterEntry, RedisListDlq, serialization
├── redis_consumer.py       BLPOP consumer bridging S2.1 Redis list → Celery
├── tasks.py                Celery task registration, retry/DLQ orchestration
├── runtime_integration.py  S2.2/S2.5 lazy composition (ExecutionContext, StateManagerTaskRecorder, GraphAgentExecutor)
└── worker_runtime.py       Production entry point (IntegratedWorker, create_integrated_worker)
```

### `celery_app.py`

Factory `create_celery_app(config)` creates a Celery app from validated
`WorkerConfig`. Maps environment-driven settings to Celery configuration:
`worker_concurrency`, `worker_prefetch_multiplier`, `task_acks_late`,
`task_reject_on_worker_lost`, `task_soft_time_limit`, `task_time_limit`,
`worker_soft_shutdown_timeout`.

### `retry_policy.py`

Pure decision logic with no external dependencies.

- `RetryDecision` enum: `RETRY`, `TERMINAL`, `EXHAUSTED`.
- `RetryPolicy(base_delay_seconds, max_delay_seconds, max_retries)`:
  - `decide(error, retries_completed)` — classifies using `error.retryable`.
    Exceptions without `retryable=True` are `TERMINAL`. Retryable errors past
    the configured limit are `EXHAUSTED`.
  - `delay_for_retry(retry_number)` — bounded exponential backoff:
    `min(base * 2^(n-1), max)`.

### `dlq.py`

- `DeadLetterEntry` — frozen dataclass retaining: original payload, optional
  execution context, error type/message, retry count, task name/id, UTC
  timestamp.
- `RedisListDlq(redis_client, queue_name)` — publishes serialized JSON evidence
  via `RPUSH` to the configured DLQ list.
- `create_dead_letter_entry(...)` — factory with validation.
- `serialize_dead_letter_entry(entry)` — JSON serialization including execution
  context (`execution_identifier`, `retry_state_id`) when present.

### `redis_consumer.py`

Bridge from S2.1's Redis list to the Celery task boundary.

- Reads from `incident_events` via blocking `BLPOP`.
- Validates the four required S2.1 fields: `sys_id`, `event_id`, `event_type`,
  `number`.
- Valid payloads are dispatched via `apply_async` with S2.2 execution context
  in Celery task headers.
- Invalid JSON or missing fields produce a `MalformedIncidentPayload` marker
  dispatched to the same task — the task handles it as a terminal failure and
  creates DLQ evidence. The consumer loop continues.
- `consume_next_incident_for_celery(redis_client, task, establish_context)` —
  single-iteration production dispatch.
- `run_incident_consumer_for_celery(...)` — production loop with
  `should_stop` callback and exception isolation per iteration.

### `tasks.py`

Registers the `process_accepted_incident` Celery task via
`register_process_accepted_incident_task(retry_policy, seams, app)`.

The task is decorated with `@celery_app.task(bind=True, shared=False)`.
See [Celery task registration reliability](#celery-task-registration-and-sharedfalsereliability) below.

Integration seams (`IntegrationSeams` dataclass):
- `agent` — `AgentExecutor` protocol (execute one incident).
- `state_recorder` — `StateRecorder` protocol (record_retry, record_failure).
- `dlq` — `DlqSink` protocol (transition one DLQ entry).
- `execution_context` — optional, carried for DLQ evidence.

Production path uses `ProductionIntegrationSeams.for_task(task)` which
reconstructs `ExecutionContext` from Celery headers and instantiates
`GraphAgentExecutor` and `StateManagerTaskRecorder` for that task invocation.

Task orchestration:
1. If payload is `MalformedIncidentPayload`, raise its stored error immediately.
2. Otherwise invoke `agent.execute(accepted_incident)`.
3. On success, call `state_recorder.record_success(...)` if available.
4. On `SoftTimeLimitExceeded`, classify as retryable.
5. On any other exception, classify using `RetryPolicy.decide()`.
6. `RETRY` → `state_recorder.record_retry(...)`, `task.retry(exc, countdown,
   headers)`. Headers are forwarded to preserve execution context across retries.
7. `TERMINAL` or `EXHAUSTED` → `state_recorder.record_failure(...)`,
   `dlq.transition(create_dead_letter_entry(...))`.

### `runtime_integration.py`

Lazy-import composition layer for S2.2 and S2.5 modules (developed on separate
branches).

- `ExecutionContext(execution_identifier, retry_state_id)` — frozen dataclass
  with `task_headers()` producing `barq_execution_identifier` and
  `barq_retry_state_id` header keys.
- `establish_execution_context(accepted_incident)` — calls S2.2
  `StateManager.create_execution(incident_reference=number)` and
  `StateManager.create_retry_state(execution_reference=uuid, attempt_count=0)`.
  Returns `ExecutionContext` with the generated identifiers. Uses
  `SessionLocal()` with proper close.
- `StateManagerTaskRecorder(context, failing_node)` — production state recorder:
  - `record_retry(...)` → `update_retry_state(retry_state_id, count+1, error)`.
  - `record_failure(...)` → `record_failure(execution_reference, ...)` +
    `update_execution_status(id, "failed")`.
  - `record_success(...)` → `update_execution_status(id, "succeeded")`.
- `GraphAgentExecutor` — invokes S2.5 `compile_graph(checkpointer)`,
  `graph.invoke({execution_id, incident_number, incident_payload}, config)`,
  wrapped with `trace_execution("execute_incident_graph")`.
- `context_from_task_headers(headers)` — reads execution context back from
  Celery request headers.

### `worker_runtime.py`

Production composition root.

- `create_integrated_worker(redis_client, config)` — assembles:
  1. `WorkerConfig` from environment.
  2. Celery app via `create_celery_app`.
  3. `RetryPolicy` from config.
  4. `RedisListDlq` connected to `config.dlq_queue`.
  5. `ProductionIntegrationSeams` with the DLQ sink.
  6. Registered `process_accepted_incident` task.
  7. Returns `IntegratedWorker(redis_client, task)`.
- `IntegratedWorker.run(should_stop)` — runs the BLPOP→Celery dispatch loop
  via `run_incident_consumer_for_celery`.

## End-to-end runtime flow

```text
S2.1 FastAPI webhook
  → RPUSH "incident_events" ticket.model_dump_json()
    (S2.1 payload: {event_id, sys_id, number, event_type, contract_version} — unchanged by S2.3)

redis_consumer.py :: consume_next_incident_for_celery()
  → BLPOP "incident_events"
  → deserialize_incident_message()
      → decode bytes/str
      → parse JSON
      → validate: sys_id, event_id, event_type, number
      → valid dict OR MalformedIncidentPayload

  [valid payload]
  → runtime_integration.py :: establish_execution_context(payload)
      → S2.2 StateManager.create_execution(incident_reference=number)
      → S2.2 StateManager.create_retry_state(execution_reference=uuid, attempt_count=0)
      → ExecutionContext(execution_identifier, retry_state_id)
        (identifiers come from S2.2, never derived from event_id/sys_id/number)
  → task.apply_async(args=(payload,), headers=context.task_headers())

  [malformed payload]
  → task.apply_async(args=(MalformedIncidentPayload,), headers=None)

tasks.py :: process_accepted_incident(task, accepted_incident)
  → ProductionIntegrationSeams.for_task(task)
      → context_from_task_headers(task.request.headers)
      → GraphAgentExecutor + StateManagerTaskRecorder + DLQ

  [valid incident]
  → GraphAgentExecutor.execute(incident)
      → S2.5 compile_graph(checkpointer)
      → S2.5 graph.invoke({execution_id, incident_number, incident_payload})
      → S2.5 trace_execution("execute_incident_graph")
  → success → StateManagerTaskRecorder.record_success()
                → S2.2 update_execution_status(id, "succeeded")

  [retryable failure, retries remaining]
  → RetryPolicy.decide() → RETRY
  → StateManagerTaskRecorder.record_retry()
      → S2.2 update_retry_state(retry_state_id, count+1, error)
  → task.retry(exc, countdown=delay, headers=SAME_HEADERS)
    (same Execution and RetryState — no new execution is created on retry)

  [terminal failure OR retries exhausted]
  → RetryPolicy.decide() → TERMINAL or EXHAUSTED
  → StateManagerTaskRecorder.record_failure()
      → S2.2 record_failure(execution_reference, failing_node, error_class, message)
      → S2.2 update_execution_status(id, "failed")
  → create_dead_letter_entry(payload, execution_context, error, ...)
  → RedisListDlq.transition() → RPUSH to DLQ list

  [malformed incident]
  → raises MalformedIncidentPayload.error (ValueError)
  → _NoExecutionStateRecorder (no S2.2 state to update)
  → DLQ entry with execution_context=None
```

## Redis queue topology

| List | Producer | Consumer | Purpose |
|---|---|---|---|
| `incident_events` | S2.1 via `RPUSH` | S2.3 via `BLPOP` | Accepted webhook payloads. Not a Celery routing queue. |
| Configured DLQ (`CELERY_DLQ_QUEUE`) | S2.3 via `RPUSH` | Operations/replay tooling | Terminal, exhausted, and malformed failure evidence. |

S2.3 also uses a Celery broker (configured via `CELERY_BROKER_URL`) for internal
task dispatch. The `incident_events` list is separate from the Celery broker
queue.

## Execution context propagation

`execution_identifier` and `retry_state_id` are created once per incident by
`establish_execution_context()` before `apply_async`. They are **never derived
from the S2.1 payload** (`event_id`, `sys_id`, or `number`). Both come
exclusively from S2.2's `create_execution` and `create_retry_state` responses.

| Step | Action |
|---|---|
| 1. Create | `establish_execution_context()` calls S2.2 and returns `ExecutionContext(execution_identifier, retry_state_id)` |
| 2. Carry | Packed into Celery headers as `barq_execution_identifier` and `barq_retry_state_id` via `context.task_headers()` |
| 3. Read back | `context_from_task_headers(task.request.headers)` inside the task |
| 4. Forward on retry | `task.retry(headers=SAME_HEADERS)` — unchanged header set |

Design guarantees:
- The **same** S2.2 Execution and RetryState are reused across all retry
  attempts. No new execution is created per retry.
- RetryState `attempt_count` is **updated** (not recreated) on each retry.
- The S2.1 webhook payload travels as task arguments and is **never modified**
  by S2.3.

## Retry behavior

### Classification

| Error characteristic | Decision | Worker action |
|---|---|---|
| `error.retryable is True`, retries < max | `RETRY` | Backoff delay, S2.2 retry state update, Celery `task.retry` |
| `error.retryable is True`, retries ≥ max | `EXHAUSTED` | S2.2 failure record, execution → `failed`, DLQ |
| `error.retryable` absent or `False` | `TERMINAL` | S2.2 failure record, execution → `failed`, DLQ |
| `SoftTimeLimitExceeded` | Retryable | Classified as retryable at the worker boundary |
| `MalformedIncidentPayload` | Terminal | No S2.2 state (no valid execution), DLQ only |

The S2.5 ServiceNow exceptions expose `retryable=True` on `ServiceNowAuthError`
and `ServiceNowServerError`. Exceptions without `retryable` are terminal.

### Bounded exponential backoff

```
delay(n) = min(base_delay * 2^(n-1), max_delay)
```

- `n` is the one-based retry number.
- First retry uses `base_delay_seconds`.
- Growth is exponential, capped at `max_delay_seconds`.
- No jitter is applied.
- Empirical verification: `test_empirical_celery_retry_intervals_follow_bounded_backoff`
  measures actual task retry intervals against the configured policy, asserting
  they fall within `[-0.15s, +2.0s]` of their expected values.

### State across retries

- One `Execution` (S2.2) per incident consumption. Never recreated on retry.
- One `RetryState` (S2.2) per execution. `attempt_count` incremented on each
  retry via `update_retry_state`.
- On exhaustion: `record_failure` + `update_execution_status` → `"failed"`.
- On success: `update_execution_status` → `"succeeded"`.

## Worker configuration

All settings are loaded from environment variables via `WorkerConfig.from_environment()`.
No defaults are assumed; missing or invalid values raise `ValueError`.

| Environment variable | Purpose | Celery mapping |
|---|---|---|
| `CELERY_BROKER_URL` | Redis broker connection | `broker` |
| `CELERY_MAIN_QUEUE` | Main work queue name | — (used by consumer, not Celery routing) |
| `CELERY_DLQ_QUEUE` | Dead-letter list name | — (used by `RedisListDlq`) |
| `CELERY_WORKER_CONCURRENCY` | Worker parallelism | `worker_concurrency` |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | Task reservation limit | `worker_prefetch_multiplier` |
| `CELERY_TASK_SOFT_TIME_LIMIT_SECONDS` | Soft timeout (raises `SoftTimeLimitExceeded`) | `task_soft_time_limit` |
| `CELERY_TASK_TIME_LIMIT_SECONDS` | Hard timeout (kills task process) | `task_time_limit` |
| `CELERY_TASK_MAX_RETRIES` | Maximum retry attempts before DLQ | `RetryPolicy.max_retries` |
| `CELERY_RETRY_BASE_DELAY_SECONDS` | Initial backoff delay | `RetryPolicy.base_delay_seconds` |
| `CELERY_RETRY_MAX_DELAY_SECONDS` | Backoff delay cap | `RetryPolicy.max_delay_seconds` |
| `CELERY_TASK_ACKS_LATE` | Late acknowledgement | `task_acks_late` |
| `CELERY_TASK_REJECT_ON_WORKER_LOST` | Reject on worker loss | `task_reject_on_worker_lost` |
| `CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | Graceful shutdown grace period | `worker_soft_shutdown_timeout` |

Validation rules:
- `task_time_limit_seconds` must be greater than `task_soft_time_limit_seconds`.
- `retry_max_delay_seconds` must be ≥ `retry_base_delay_seconds`.
- Concurrency, prefetch, and time limits must be positive integers.
- Retry values must be non-negative integers.

## DLQ evidence and replay

### Evidence structure

Each DLQ entry (`DeadLetterEntry`) retains:

- Original S2.1 payload (unchanged).
- Execution context when present (`execution_identifier`, `retry_state_id`).
  `None` for malformed payloads that never received an execution.
- Error class name and message.
- Retry count at the point of failure.
- Celery task name and task ID.
- Timezone-aware UTC timestamp.

Published to the configured DLQ Redis list as compact JSON via `RPUSH`.

### Replay

`replay_dlq_payload(payload, process)` passes a preserved DLQ payload to an
injected processor exactly once. It performs no retry, no execution creation, and
no DLQ transition itself. The replay requirement is demonstrated in tests:

- `test_dependency_fix_replays_exhausted_dlq_entry_once_without_new_failures`
  exercises the full local recovery sequence: retryable failure → exhaustion →
  DLQ → dependency repair → replay to success. Original retry/failure records
  and the DLQ entry are unchanged after replay.

A production replay endpoint or re-enqueue path remains owned by S2.1/operations.

## Celery task registration and `shared=False` reliability

`register_process_accepted_incident_task()` registers the dynamically-created
`process_accepted_incident` task with:

```python
@celery_app.task(bind=True, shared=False)
def process_accepted_incident(task: Task, accepted_incident: object) -> object:
    ...
```

**Why `shared=False` is necessary.**

Celery's default `shared=True` registers a `cons` closure — capturing the
function and its closure variables, including the injected `seams` object — into
a process-global registry (`celery._state._on_app_finalizers`). Whenever any
subsequent Celery app is finalized (e.g., during `app.finalize()` at worker
startup), Celery replays all entries in `_on_app_finalizers` against the new app.
In a test process where multiple Celery apps are created and finalized in
sequence, a `cons` from an earlier registration (e.g., one carrying
`ProductionIntegrationSeams`) would overwrite the later app's task registration
(e.g., one carrying test `IntegrationSeams`), causing the worker to execute
the wrong task body.

`shared=False` prevents the closure from entering `_on_app_finalizers`. The
task is registered only with its intended Celery app. **No production behavior
changes:** in production, `register_process_accepted_incident_task` is called
exactly once per worker process, so the global finalizer registry is never
re-applied.

This fixed a confirmed cross-test global-state contamination that caused
`test_empirical_celery_retry_intervals_follow_bounded_backoff` to report
zero agent attempts when run after `test_worker_runtime_integration.py` in
the full suite.

## Reliability boundaries

| Concern | Implementation |
|---|---|
| Malformed messages | `MalformedIncidentPayload` wraps invalid JSON/payloads. The consumer loop continues. The task records a terminal failure and DLQ entry. |
| Poison job isolation | Bounded retries (`max_retries`), task time limits (soft + hard), DLQ routing. One failing event does not block healthy events. |
| Task timeout | `SoftTimeLimitExceeded` is caught and classified as retryable. Hard `TimeLimitExceeded` terminates the task process; under the current classification it is terminal and produces a DLQ entry. |
| Graceful shutdown | `worker_soft_shutdown_timeout` is set from `CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS`. Celery owns the `SIGTERM` lifecycle — S2.3 does not install custom signal handlers. The BLPOP consumer loop checks `should_stop()` between iterations. |
| Late acknowledgement | `task_acks_late=True` + `task_reject_on_worker_lost=True` ensures uncompleted tasks are redelivered on worker loss, compatible with S2.2 idempotency. |
| Consumer loop resilience | Each `consume_next_incident_for_celery` iteration is wrapped in a try/except. Redis transport failures are logged and do not crash the loop. |
| Worker saturation | Concurrency and prefetch are configuration-driven. S2.3 does not claim webhook latency or throughput benchmarks — those are S2.1 integration concerns. |

## S2.5 invocation boundary

| Element | Contract |
|---|---|
| Invocation | `GraphAgentExecutor.execute(accepted_incident, execution_id, incident_number)` |
| Graph composition | `compile_graph(checkpointer=get_checkpointer())` |
| Graph input | `{execution_id, incident_number, incident_payload}` with `config={configurable: {thread_id: execution_id}}` |
| Tracing | `trace_execution("execute_incident_graph")` decorator from S2.5 observability |
| Success | Graph result returned from Celery task; S2.3 records `succeeded` |
| Failure | Exceptions with `retryable=True` are retried; others are terminal |
| Timeout | S2.3 handles Celery timeout lifecycle; graph-specific cancellation is S2.5-owned |

## S2.2 persistence boundary

| Operation | S2.3 call | S2.2 API |
|---|---|---|
| Create execution | `establish_execution_context()` | `StateManager.create_execution(incident_reference=number)` |
| Create retry state | `establish_execution_context()` | `StateManager.create_retry_state(execution_reference=uuid, attempt_count=0)` |
| Record retry | `StateManagerTaskRecorder.record_retry()` | `StateManager.update_retry_state(retry_state_id, count+1, error)` |
| Record failure | `StateManagerTaskRecorder.record_failure()` | `StateManager.record_failure(execution_reference, failing_node, error_class, message, retry_count)` |
| Mark failed | `StateManagerTaskRecorder.record_failure()` | `StateManager.update_execution_status(id, "failed")` |
| Mark succeeded | `StateManagerTaskRecorder.record_success()` | `StateManager.update_execution_status(id, "succeeded")` |

S2.3 never derives identifiers from the S2.1 event payload. All identifiers
come from S2.2's `create_execution` response. S2.2 retains ownership of schema,
sessions, transactions, and migrations.

## Final verification

All evidence below is from the S2.3 branch (`feature/s2.3-celery-workers`)
as of the final cleanup and `shared=False` fix.

| Check | Result |
|---|---|
| S2.3 focused test suite | **78 passed, 0 failed** |
| Empirical retry/backoff test in isolation | **PASSED** (`test_empirical_celery_retry_intervals_follow_bounded_backoff`) |
| Full suite including empirical test | **78 passed, 0 failed** (no regression after `shared=False` fix) |
| `git diff --check` | **Clean** — no whitespace errors (LF/CRLF warnings only, not errors) |
| Celery global-state contamination | **Identified and fixed** — `shared=False` eliminates `_on_app_finalizers` cross-test contamination |
| Environment-driven concurrency | **Verified** — `WorkerConfig.from_environment()` with no hard-coded values |
| DLQ dependency-fix replay | **Verified** — `test_dependency_fix_replays_exhausted_dlq_entry_once_without_new_failures` |
| Malformed payload isolation | **Verified** — `MalformedIncidentPayload` path produces DLQ entry; consumer loop continues |
| Retry execution context propagation | **Verified** — headers forwarded unchanged through `task.retry(headers=SAME_HEADERS)` |

Webhook saturation benchmarks (p95 latency under load) are S2.1-owned evidence.
No such benchmark is claimed by or attributed to S2.3.

## Sarah review / acceptance coverage

This section maps S2.3 to the Sprint 2.3 acceptance requirements.

### 1. Environment-driven worker concurrency

All worker configuration is environment-variable driven via `WorkerConfig.from_environment()`.
`CELERY_WORKER_CONCURRENCY`, `CELERY_WORKER_PREFETCH_MULTIPLIER`, time limits,
retry parameters, and queue names are all read from the environment. No value is
hard-coded. Tested in `tests/test_worker_config.py`.

### 2. Concrete DLQ replay after dependency recovery

Tested in `test_retry_dlq.py::test_dependency_fix_replays_exhausted_dlq_entry_once_without_new_failures`:
- Agent fails with a retryable error on every attempt.
- After retry exhaustion, a DLQ entry is created with full context.
- The root dependency is repaired (test double updated).
- `replay_dlq_payload()` executes the payload against the repaired agent.
- The replay succeeds with no additional DLQ entries and no mutation of original
  retry/failure records.

### 3. Webhook latency under worker saturation

**This benchmark is S2.1-owned evidence, not an S2.3 benchmark.**

S2.1 (`origin/s2.1/fast-api-webhook`) committed `tests/load/results/sustained_load_stats.csv`
showing p50=14ms, p95=51ms at 92,010 webhook requests, and a queue-depth curve
showing p95=51–55ms across queue depths 0 to 50,000. S2.3 does not own, operate,
or re-claim these results. S2.3 documents that `CELERY_WORKER_CONCURRENCY` and
`CELERY_WORKER_PREFETCH_MULTIPLIER` are environment-configurable to prevent
worker saturation from being a fixed ceiling.

### 4. Task timeout and poison-job isolation

- Soft timeout (`CELERY_TASK_SOFT_TIME_LIMIT_SECONDS`): `SoftTimeLimitExceeded`
  caught and classified as a retryable error — enters bounded backoff path.
- Hard timeout (`CELERY_TASK_TIME_LIMIT_SECONDS`): terminates the task process;
  classified as terminal → DLQ.
- Bounded retries enforce a ceiling on how long one poison job can occupy the
  retry queue before being dead-lettered.
- Tested in `tests/test_retry_dlq.py` (timeout classification) and
  `tests/test_worker_tasks.py` (poison job isolation under concurrent load).

### 5. Retry-state and final execution-status recording

On every retry: `StateManagerTaskRecorder.record_retry()` increments
`RetryState.attempt_count` via S2.2 `update_retry_state`.
On failure/exhaustion: `record_failure()` writes the `Failure` record and sets
`Execution.status = "failed"` via S2.2 `update_execution_status`.
On success: `record_success()` sets `Execution.status = "succeeded"`.
Tested in `tests/test_worker_runtime_integration.py`.

### 6. Graceful SIGTERM / shutdown behavior

`task_acks_late=True` and `task_reject_on_worker_lost=True` ensure a task that
is in-flight when the worker receives SIGTERM is redelivered rather than lost.
`CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS` controls the soft-shutdown grace period.
The BLPOP consumer loop checks `should_stop()` between iterations, ensuring the
loop exits cleanly without dropping the next-iteration payload. Celery manages
the SIGTERM lifecycle; S2.3 does not install custom signal handlers. Tested in
`tests/test_worker_redis_consumer.py`.

## Handoff / external dependencies

S2.3 is complete. The following integration items are external to S2.3 and owned
by other workstreams. They are documented here for integration planning, not as
S2.3 defects.

- **S2.5 node wiring (S2.5 team):** `load_node`, `retrieve_node`, and
  `generate_node` in the S2.5 branch are currently stubs. At integration time,
  S2.5 must wire these to the real S1.5 ServiceNow OAuth client and the S2.4
  hybrid retrieval implementation. S2.3 is unaffected — it invokes
  `GraphAgentExecutor.execute()` regardless of what the graph nodes do
  internally.

- **`GraphAgentExecutor` import path (S2.5/S2.3 joint):** S2.3's production
  seams import `GraphAgentExecutor` from `src.agent.graph`. The S2.5 branch
  currently places `GraphAgentExecutor` in `src/workers/tasks.py`. These import
  paths must be aligned at integration/merge time. S2.3's integration seams
  design requires only that `GraphAgentExecutor` satisfies the `AgentExecutor`
  protocol — the resolution is a module placement decision, not a protocol
  change.

- **S2.1 webhook route contract (S2.1 team):** The S2.1 branch routes the
  webhook at `POST /webhook` rather than the contract-specified
  `POST /api/v1/webhook/incident`. This requires confirmation or correction by
  the S2.1 team. S2.3 consumes from `incident_events` regardless of the HTTP
  path — no S2.3 changes are required.

- **Production HTTP DLQ replay (S2.1/operations):** The `POST /api/v1/dlq/{event_id}/replay`
  endpoint in S2.1 is currently a stub with a TODO to re-enqueue to Redis once
  S2.3 lands. Wiring this endpoint to the S2.3 DLQ format and Redis list is
  S2.1/operations-owned. S2.3's DLQ evidence structure retains the original
  payload and full context to support this.

- **Branch integration:** All Sprint 2 branches remain unmerged as of this
  handoff. Final Sprint 2 integration requires assembling the branches and
  validating the end-to-end flow across S2.1 → S2.3 → S2.2/S2.5.

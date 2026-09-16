# Sprint 2 Worker Topology and Configuration Contract

## Purpose and ownership

This document defines the intended S2.3 Redis/Celery worker topology and the
configuration contract needed to operate it. It records integration boundaries;
it does not introduce worker code, database schema, API behavior, agent logic,
or production configuration values.

| Area | Owner | S2.3 responsibility |
|---|---|---|
| Webhook acceptance and enqueue request | S2.1 | No; S2.3 consumes the accepted event at the worker boundary. |
| PostgreSQL workflow state, idempotency, retry state, and failures | S2.2 | No; S2.3 calls the interface supplied by S2.2. |
| Redis queue topology, Celery worker configuration, retries, time limits, DLQ, and shutdown | S2.3 | Yes. |
| Retrieval and reranking | S2.4 | No. |
| Agent runtime, LangGraph graph, nodes, state, and tracing | S2.5 | No; S2.3 invokes the agreed graph entry point. |

## Intended flow

```text
ServiceNow event
  -> S2.1 webhook acceptance and validation
  -> S2.1 enqueue to the configured main Celery queue in Redis
  -> S2.3 Celery worker task boundary
  -> S2.5 graph invocation
  -> success: return task result and update state through the agreed S2.2 interface
  -> retryable failure: record attempt through S2.2, calculate S2.3 backoff, retry
  -> terminal or exhausted failure: record failure through S2.2, publish to DLQ
  -> DLQ replay: re-enqueue the preserved event only through the agreed replay contract
```

The S1.3 event contract currently identifies the inbound event payload as
`event_id`, `sys_id`, `number`, and `event_type`. S2.3 must preserve the
complete accepted payload when retrying or dead-lettering it. S2.3 must not add
incident data to that contract.

## Queue roles

| Queue role | Purpose | Required behavior | Status |
|---|---|---|---|
| Main work queue | Carries accepted events to the Celery task boundary. | Healthy events are processed independently from failing events. | Queue name and route are pending S2.1/S2.3 agreement. |
| Dead-letter queue (DLQ) | Holds malformed, terminal, and retry-exhausted events for investigation and replay. | Preserve the original accepted payload and failure context. It must not silently discard a failed event. | Queue name, message envelope, retention, and access controls are pending team agreement. |
| Optional replay path | Returns a remediated DLQ event to the main queue. | Preserve the original event identity and create a traceable new processing attempt through S2.2. | Blocked by S2.1 enqueue and S2.2 retry/failure contracts. |

The DLQ is an isolation mechanism, not a substitute for bounded retries or
durable failure state. S2.3 will not create an independent PostgreSQL failure
model; S2.2 owns that persistence.

## Retry, backoff, and failure flow

1. The worker invokes the S2.5 graph through the agreed task boundary.
2. S2.3 classifies a raised failure as retryable or terminal using deterministic
   rules and the agreed exception taxonomy.
3. For a retryable failure below the configured retry limit, S2.3 records the
   attempt through S2.2, calculates a bounded exponential delay, and reschedules
   the same event.
4. For a terminal failure, malformed event, or retryable failure that has used
   the configured retry limit, S2.3 records the failure through S2.2 and sends
   the payload plus failure context to the DLQ.
5. A replay, after its root cause is fixed, must use the agreed S2.1 enqueue and
   S2.2 state-transition contracts. Replay behavior must be idempotent.

### Failure classes

| Class | Meaning | Worker action |
|---|---|---|
| Retryable | A transient dependency, transport, or service failure with a reasonable expectation that another attempt can succeed. | Retry with configured exponential backoff until exhausted. |
| Terminal | Malformed input, validation failure, authorization failure, unsupported event, or another failure that cannot succeed merely by retrying. | Send directly to the DLQ. |
| Exhausted | A retryable failure after the configured retry limit has been reached. | Send to the DLQ. |

The final exception list remains pending the S2.5 graph exception contract.
Existing ServiceNow client exceptions expose a `retryable` attribute, but that
does not define the complete worker-level taxonomy.

### Backoff requirements

The retry calculation must be deterministic and testable. It must use the
configured base delay and retry attempt number, grow exponentially, and cap at
the configured maximum delay when one is supplied. Jitter policy is pending
team agreement; no jitter behavior is assumed by this document.

## Task timeout requirements

Each worker task needs a configured execution limit so a stuck or poison event
cannot occupy a worker indefinitely. The final implementation must distinguish:

| Limit | Purpose | Status |
|---|---|---|
| Soft task time limit | Gives task code an opportunity to stop or raise a controlled timeout. | Value and graph cancellation expectations are pending S2.5/S2.3 agreement. |
| Hard task time limit | Terminates work that exceeds the permitted execution window. | Value and relationship to the soft limit are pending team agreement. |

Timeouts must enter the same S2.3 classification, retry, and DLQ process as
other failures. Whether a timeout is retryable depends on the agreed failure
taxonomy and must not be guessed.

## Worker reliability requirements

| Concern | Requirement | Decision still needed |
|---|---|---|
| Concurrency | Read worker concurrency from configuration; never hard-code it. Configure enough parallelism that one failing event does not serialize healthy events. | Environment variable name and deployment-specific value. |
| Prefetch | Configure bounded prefetch so a single worker does not reserve excessive work while long-running tasks execute. | Prefetch multiplier value. |
| Acknowledgement | Accepted work must not be silently dropped on worker loss. Celery acknowledgement behavior must be selected to support recovery of uncompleted work. | Exact late-acknowledgement and worker-loss rejection settings, validated against S2.2 idempotency semantics. |
| Poison-event isolation | Bound retries and task duration; route terminal/exhausted events out of the main processing path. | Main/DLQ routing names and retry values. |
| Graceful shutdown | On `SIGTERM`, the worker should stop accepting new work, allow active work to follow configured timeout behavior, and leave uncompleted accepted work recoverable. | Shutdown grace period and Docker worker-service configuration. |
| Observability | Log queue/task identity, attempt number, classification, retry delay, timeout, and DLQ transition without leaking credentials or incident content. | Structured logging format and S2.5 tracing boundary. |

## Required S2.3 configuration values

The following values are needed before worker code can be finalized. The names
below are proposed environment-variable names only. **No production defaults or
values have been selected.**

| Proposed environment variable | Required for | Value status |
|---|---|---|
| `CELERY_BROKER_URL` | Redis broker connection. | Pending environment-specific URL. |
| `CELERY_RESULT_BACKEND` | Celery result backend, if task results are retained. | Pending decision whether a result backend is required and which backend to use. |
| `CELERY_MAIN_QUEUE` | Main work-queue name. | Pending S2.1/S2.3 routing agreement. |
| `CELERY_DLQ_QUEUE` | Dead-letter queue name. | Pending S2.3 operational agreement. |
| `CELERY_WORKER_CONCURRENCY` | Worker process/thread concurrency. | Pending deployment capacity decision. |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | Worker reservation limit. | Pending workload/throughput decision. |
| `CELERY_TASK_SOFT_TIME_LIMIT_SECONDS` | Controlled task timeout. | Pending S2.3/S2.5 graph-runtime decision. |
| `CELERY_TASK_TIME_LIMIT_SECONDS` | Hard task timeout. | Pending S2.3/S2.5 graph-runtime decision. |
| `CELERY_TASK_MAX_RETRIES` | Maximum retry attempts before DLQ. | Pending reliability policy decision. |
| `CELERY_RETRY_BASE_DELAY_SECONDS` | Initial exponential-backoff delay. | Pending reliability policy decision. |
| `CELERY_RETRY_MAX_DELAY_SECONDS` | Upper bound for exponential backoff. | Pending reliability policy decision. |
| `CELERY_RETRY_JITTER` | Whether and how retry delay jitter is applied. | Pending reliability policy decision. |
| `CELERY_TASK_ACKS_LATE` | Acknowledgement timing. | Pending validation with S2.2 idempotency guarantees. |
| `CELERY_TASK_REJECT_ON_WORKER_LOST` | Recovery behavior if a worker process is lost. | Pending validation with S2.2 idempotency guarantees. |
| `CELERY_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | Grace period for controlled worker shutdown. | Pending Docker/deployment decision. |

Configuration parsing must reject invalid values clearly. Secrets must remain in
the local `.env`, not in source, logs, tests, or this document.

## Required integration contracts

### S2.1: FastAPI/webhook/enqueueing

S2.3 needs:

- The final accepted-event payload and validation behavior.
- The Celery task name, queue route, and enqueueing call used after acceptance.
- The behavior when Redis is unavailable while S2.1 accepts an event.
- The authorized entry point for DLQ replay, if replay is exposed through an API.

S2.3 will not implement webhook routes, API endpoints, or producer-side
idempotency.

### S2.2: PostgreSQL state and failures

S2.3 needs an interface that can:

- Record a processing attempt and retry count for an event identity.
- Record a classified failure, safe failure context, and DLQ transition.
- Identify whether replay is permitted and record a replay attempt.
- Provide transaction/idempotency semantics compatible with late acknowledgement
  and redelivery.

S2.3 will not create migrations, models, or a parallel retry/failure store.

### S2.5: agent and LangGraph execution

S2.3 needs:

- The importable graph invocation callable and its input contract.
- Its success result contract.
- A documented exception taxonomy, including retryability and timeout/cancellation
  behavior.
- Any required cleanup behavior after soft timeout or worker shutdown.

S2.3 owns only the Celery execution boundary around that callable. It will not
implement agent state, graph nodes, model initialization, tracing, or ServiceNow
business logic.

## Pending decisions before implementation

1. Approve the proposed configuration variable names and supply environment
   values/default policy.
2. Agree the main-queue, DLQ, and task-routing names with S2.1.
3. Agree the retry limits, delays, cap, and jitter policy.
4. Agree timeout and shutdown values with S2.5 and deployment owners.
5. Receive S2.2 retry/failure and replay interfaces.
6. Receive S2.5 graph invocation and exception contracts.


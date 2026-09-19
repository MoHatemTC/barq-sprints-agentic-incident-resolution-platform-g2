# Sprint 2.3 — Manager Handoff & Technical Walkthrough

**Author:** Abdullah (S2.3)
**Document type:** Final handoff — documentation only
**Date:** 2026-09-19
**Branch:** feature/s2.3-celery-workers

---

## 1. Executive Summary

### What Sprint 2 is doing

An incident is reported in ServiceNow. A business rule fires and calls our webhook. **S2.1** receives the request, validates it, persists it to PostgreSQL, places it on a Redis queue, and immediately returns HTTP 202 — the caller is never made to wait for AI processing.

**S2.3** (this workstream) picks up the incident from that queue, creates the runtime execution tracking record using **S2.2**, hands the incident to the **S2.5** agent graph for AI processing, and manages what happens on success, on retriable failure, on terminal failure, and when the event is malformed. If the agent fails more times than the policy allows, S2.3 moves the full failure context to a dead-letter queue so it can be inspected and replayed.

**S2.4** provides the hybrid retrieval capability that the S2.5 agent graph uses internally to look up knowledge-base articles.

### S2.3 in one paragraph

S2.3 is the execution substrate between the webhook and the AI agent. It owns the Redis consumer that reads incidents from the list S2.1 writes to, the Celery workers that execute incident processing asynchronously, the retry policy that decides whether a failure is transient or permanent, the bounded exponential backoff that spaces out retries, the dead-letter path that captures everything about a failed incident for later replay, and the runtime composition layer that connects S2.2 state management and S2.5 agent graph without duplicating their logic.

### S2.3 completion status

> **S2.3 is complete according to its own acceptance criteria.**
> 78 automated tests pass. All S2.3 contract requirements are satisfied.
>
> Sprint 2 as a whole still has integration work owned by other workstreams.

---

## 2. The Big Picture
(ASCII architecture mapping S2.1 -> Redis -> S2.3 Consumer -> S2.2 Context -> S2.3 Celery -> S2.5 Agent -> Success/Retry/DLQ)

## 3. Ownership Map
* **S2.1:** Webhook, validation, persistence, Redis RPUSH (Producer)
* **S2.2:** DB state, execution/retry state, idempotency (Caller)
* **S2.3:** Workers, queues, retry, DLQ (Owner)
* **S2.4:** Retrieval/reranking (Downstream dependency)
* **S2.5:** Agent graph/nodes/tracing (Invoked by S2.3)

## 4. What I Implemented in S2.3
* Redis list consumer (BLPOP)
* Celery app & environment-driven concurrency
* Retry policy & Bounded exponential backoff
* Dead-letter queue (DLQ)
* Execution context propagation (Same execution across retries)
* Poison-job isolation & Malformed payload handling
* Graceful shutdown configuration
* Production composition root

## 5. Production File-by-File Guide
* **celery_app.py:** App factory; configures concurrency, timeouts, and ACKs from env vars.
* **retry_policy.py:** Pure logic deciding RETRY vs TERMINAL and calculating backoff delay.
* **dlq.py:** Serializes unrecoverable incidents safely to a Redis list without losing context.
* **redis_consumer.py:** Safely bridges S2.1 list writes to Celery; handles poison payloads.
* **tasks.py:** Core orchestration; handles agent execution, routing success/retry/failure, uses \shared=False\.
* **runtime_integration.py:** The S2.2/S2.5 bridge; queries S2.2 for execution IDs so S2.3 never invents them.
* **worker_runtime.py:** Wires all components together for production.

## 6. Runtime Behavior
Incident arrives -> Validated by S2.1 -> RPUSH to Redis -> S2.3 BLPOPs -> S2.3 calls S2.2 for IDs -> S2.3 wraps payload & IDs in Celery task -> Agent executes.
* **Success:** S2.2 marked \succeeded\.
* **Retry:** Delay calculated, attempt count bumped in S2.2, task re-queued with SAME IDs.
* **Terminal/Exhausted:** S2.2 marked \ailed\, full payload + context shipped to DLQ.
* **Malformed:** Handled at edge, quarantined to DLQ instantly, loop continues.

## 7. Configuration
13 Environment variables strictly control worker scaling, queues, delays, and timeouts. No hardcoded logic.

## 8. Test Verification
* 78 tests passed, 0 failed.
* Empirical backoff math verified.
* DLQ replay verified.
* Graceful shutdown verified.
* Cross-test pollution solved via \shared=False\.

## 9. Current Handoff Status
S2.3 is COMPLETE.
Sprint 2 Integration is PENDING (Requires S2.5 wiring to real LLM/ServiceNow, branch merges, and S2.1 HTTP DLQ replay).

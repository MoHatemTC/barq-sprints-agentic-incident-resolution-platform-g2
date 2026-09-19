# Sprint 2 — PostgreSQL State Schema, Migrations & Idempotency

## 1. Scope

Sprint 2 implements the PostgreSQL state layer for the Agentic Incident
Resolution Platform.

PostgreSQL is the system of record for platform execution state, including:

- inbound events
- idempotency keys
- executions
- workflow checkpoints
- approvals
- failures
- retry state

The vector store is used for knowledge retrieval and is not used as the
authoritative store for workflow or execution state.

---

## 2. PostgreSQL State Schema

The Sprint 2 schema contains seven application state tables:

| Table | Purpose |
|---|---|
| `events` | Stores inbound incident events |
| `idempotency_keys` | Prevents duplicate event processing |
| `executions` | Stores workflow execution state |
| `workflow_state` | Stores serialized workflow checkpoints |
| `approvals` | Stores human approval decisions |
| `failures` | Stores workflow failure information |
| `retry_state` | Stores retry scheduling information |

An Alembic migration creates the schema:

`416136e54c32_create_sprint2_state_schema.py`

The migration supports both forward and reverse execution.

---

## 3. Entity Relationship Diagram

![Sprint 2 State Schema ERD](sprint2_state_schema_erd.png)

The diagram represents logical application-level relationships between
the state tables.

The current schema uses reference columns such as
`execution_reference` and `incident_reference`. These relationships are
not implemented as SQL `FOREIGN KEY` constraints in the current migration.

---

## 4. Events

The `events` table represents an inbound event received by the platform.

Important fields include:

- `event_identifier`
- `incident_sys_id`
- `incident_number`
- `event_type`
- `contract_version`
- `received_at`

`event_identifier` identifies the inbound event and is used together with
the idempotency table to prevent duplicate processing.

`incident_sys_id` is indexed to support incident-based queries.

---

## 5. Idempotency Enforcement

The `idempotency_keys` table contains one record for each accepted event
identifier.

The `event_identifier` column has a database-level unique constraint.

The application does not perform:

1. SELECT to check whether the event exists
2. INSERT if it does not exist

Instead, it attempts the INSERT directly and allows PostgreSQL to arbitrate
concurrent access.

Implementation:

```python
idempotency_key = IdempotencyKey(
    event_identifier=event_identifier
)

db.add(idempotency_key)

try:
    db.commit()
    return True

except IntegrityError:
    db.rollback()
    return False
```

A successful insert means the event is being processed for the first time.

A unique-constraint `IntegrityError` means another worker already accepted
the same event.

The failed transaction is rolled back and the event is reported as a
duplicate.

---

## 6. Concurrency Verification

`tests/test_idempotency.py` contains a concurrent replay test.

Two worker threads use independent database sessions and synchronize on a
barrier before attempting to insert the same event identifier.

The test verifies:

```text
Worker A → accepted
Worker B → rejected
Database → exactly one idempotency key
```

The assertions are:

```python
assert errors == []
assert results.count(True) == 1
assert results.count(False) == 1
assert len(keys) == 1
```

This verifies duplicate suppression under concurrent worker access.

---

## 7. Execution State

Each accepted event creates an `Execution`.

An execution contains:

- `execution_identifier`
- `incident_reference`
- `status`
- `node_reached`
- `model_name`
- `agent_version`
- `started_at`
- `ended_at`

The `execution_identifier` is a unique identifier for the workflow
execution.

It is also the reference used by workflow checkpoints, approvals,
failures, and retry state.

---

## 8. Workflow State

`workflow_state` stores serialized workflow checkpoints.

Each checkpoint is associated with an execution using:

```text
workflow_state.execution_reference
    →
executions.execution_identifier
```

This allows the workflow to persist progress and recover the latest
checkpoint for an execution.

---

## 9. Approvals

The `approvals` table records human governance decisions.

It stores:

- execution reference
- evidence presented
- reviewer decision
- decision timestamp
- reviewer identity

Approval records are intended to be immutable after creation.

The approval service creates a record rather than modifying an existing
decision.

The approval tests verify that attempting to overwrite an existing
approval decision is rejected.

---

## 10. Failures

The `failures` table records execution failures.

It stores:

- execution reference
- failing node
- error class
- error message
- retry count

Failures provide historical evidence of why an execution failed.

---

## 11. Retry State

The `retry_state` table stores retry information for an execution.

It contains:

- execution reference
- attempt count
- last error
- next attempt time

The execution identifier remains the stable reference across retry
attempts for the same workflow execution.

---

## 12. Audit Query Layer

The PostgreSQL state tables provide the information required to reconstruct
an execution timeline.

A basic execution query is:

```sql
SELECT
    execution_identifier,
    incident_reference,
    status,
    node_reached,
    model_name,
    agent_version,
    started_at,
    ended_at
FROM executions
WHERE execution_identifier = '<EXECUTION_ID>';
```

Workflow checkpoints can be queried using:

```sql
SELECT
    execution_reference,
    checkpoint,
    created_at,
    updated_at
FROM workflow_state
WHERE execution_reference = '<EXECUTION_ID>'
ORDER BY created_at;
```

Failures can be queried using:

```sql
SELECT
    execution_reference,
    failing_node,
    error_class,
    message,
    retry_count
FROM failures
WHERE execution_reference = '<EXECUTION_ID>';
```

Approval evidence can be queried using:

```sql
SELECT
    execution_reference,
    evidence_presented,
    reviewer_decision,
    decision_timestamp,
    reviewer_identity
FROM approvals
WHERE execution_reference = '<EXECUTION_ID>'
ORDER BY decision_timestamp;
```

Retry information can be queried using:

```sql
SELECT
    execution_reference,
    attempt_count,
    last_error,
    next_attempt_time
FROM retry_state
WHERE execution_reference = '<EXECUTION_ID>';
```

Together, these queries provide execution nodes, sequence information,
timing, evidence, decisions, retry information, and failure information.

---

## 13. Replay Protection

The workflow performs idempotency enforcement before creating an execution.

The execution flow is:

```text
Inbound Event
     |
     v
Idempotency INSERT
     |
     +---- duplicate ----> Stop
     |
     v
Create Execution
     |
     v
Workflow Processing
```

Therefore, a replayed event does not create a second execution.

The workflow test verifies that replaying the same event results in:

```text
First event  → started
Second event → duplicate
Executions   → exactly 1
```

The ServiceNow integration test additionally verifies the replay behavior
against the ServiceNow execution-log integration.

---

## 14. State and Retrieval Separation

PostgreSQL and the vector store have different responsibilities.

PostgreSQL is responsible for authoritative platform state:

- event processing
- execution state
- workflow checkpoints
- approvals
- failures
- retries
- idempotency

The vector store is responsible for knowledge retrieval.

This separation prevents workflow state from depending on semantic-search
storage and allows execution state to be queried deterministically using
relational queries.

---

## 15. Qdrant State Inspection

The Sprint 2 requirement calls for inspection of Qdrant payloads to verify
that application execution state is not stored in the vector store.

A local Qdrant inspection was attempted using the configured Qdrant
endpoint, but the configured endpoint was not running during verification.

Therefore, this repository does **not** claim successful Qdrant payload
inspection evidence.

The architectural requirement remains that PostgreSQL is the authoritative
store for application state and Qdrant is reserved for retrieval data.

---

## 16. Data Retention

PostgreSQL execution and audit data should be retained according to the
platform's operational and compliance requirements.

The current Sprint 2 implementation establishes the state schema but does
not implement an automated deletion or archival job.

Any production retention automation should preserve sufficient execution
history for incident investigation and audit requirements while applying
the organization's approved retention period.

---

## 17. Migration Verification

The migration was verified using:

```powershell
py -m alembic downgrade base
py -m alembic upgrade head
py -m alembic check
```

The final Alembic check reported:

```text
No new upgrade operations detected.
```

The resulting PostgreSQL database contains:

```text
approvals
events
executions
failures
idempotency_keys
retry_state
workflow_state
```

plus the Alembic metadata table:

```text
alembic_version
```

---

## 18. Test Verification

The complete test suite was executed successfully.

Result:

```text
86 passed
```

The Sprint 2-specific replay and idempotency tests also passed:

```text
7 passed
```

These tests cover:

- first-event acceptance
- duplicate-event rejection
- concurrent duplicate suppression
- workflow execution creation
- workflow replay protection
- workflow failure recording
- ServiceNow replay behavior

---

## 19. Definition of Done

Sprint 2 is satisfied when:

- PostgreSQL contains all seven required state tables.
- Forward migrations execute successfully.
- Reverse migrations execute successfully.
- Alembic reports no pending schema changes.
- Event identifiers are protected by a database-level unique constraint.
- Concurrent duplicate events produce exactly one accepted event.
- Duplicate events do not create secondary executions.
- Execution state is stored in PostgreSQL.
- Approval records cannot be overwritten.
- Workflow, failure, approval, and retry state can be queried by execution.
- The ER diagram is committed with the documentation.

Current automated verification:

```text
86 tests passed
```

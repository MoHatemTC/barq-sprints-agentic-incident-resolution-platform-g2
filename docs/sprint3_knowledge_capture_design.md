# Sprint 3.5 — Human-Resolution Knowledge Capture Design

## 1. Overview

Sprint 3.5 closes the human-resolution knowledge loop for incidents that cannot be safely resolved automatically.

The flow is:

Incident
→ Agent escalation
→ Human approval/resolution
→ Article Composer
→ ServiceNow publish
→ ServiceNow read-back verification
→ Qdrant ingestion
→ Future retrieval

The human-provided resolution is preserved as reusable knowledge while maintaining a distinction between human-resolution write-backs and the existing curated knowledge corpus.

## 2. Approval Schema Extension

The approval schema was extended with an optional `human_solution` field.

```text
ApprovalDecision
├── action
├── reviewer
├── rationale
└── human_solution
```

`human_solution` is intentionally separate from:
- `rationale`
- `evidence_presented`

The field represents the actual operator-provided resolution and is used as the authoritative input for knowledge capture.

The database `approvals` table contains:

```text
human_solution TEXT NULL
consumed BOOLEAN
```

The `consumed` field supports one-time approval consumption for protected tool execution.

## 3. Human Resolution Flow

When the graph reaches the human-review node, LangGraph interrupts execution and preserves the execution state through the configured checkpointer.

The approval flow supplies the human resolution.

The same execution/thread identifier is then used to resume the graph.

The relevant state contains:

```text
execution_id
incident_number
incident_payload
human_review_required
failure_reason
human_solution
```

This prevents the resolution from becoming detached from the original incident execution.

## 4. Article Composer

`src/agent/article_composer.py` performs a dedicated LLM call.

Inputs:

```text
incident snapshot
human solution
```

Output:

```text
title
summary
steps
```

The generated article is structured into a descriptive title, incident context, and numbered procedural resolution steps.

### Faithfulness requirements

The Article Composer prompt explicitly requires:

1. Use only information supported by the incident snapshot and human solution.
2. Do not invent technical details, commands, causes, systems, or configuration.
3. Treat the human solution as the authoritative resolution.
4. Do not guess missing technical details.
5. Do not introduce unsupported root causes or remediation steps.

Tests verify that minimal inputs do not result in unsupported technical information.

## 5. Article Metadata and Trust Calibration

Human-resolution articles use the canonical article metadata schema:

```text
category
service
workflow_state
version
security_level
section
```

Human-resolution articles are marked with:

```text
workflow_state = published
section = Human Resolution
security_level = internal
version = 1
```

The `Human Resolution` section distinguishes these articles from the curated corpus.

This distinction is important for trust calibration: the article represents an operator-provided resolution captured from an incident rather than an independently curated knowledge article.

The article is still usable as retrieval evidence after successful publication and ingestion, but its origin remains explicitly represented in its metadata.

## 6. ServiceNow Publication

Knowledge capture reuses the existing ServiceNow publication path:

```text
src/retrieval/publish_kb.py
```

No parallel publishing implementation is introduced.

The publication path performs the established write operation followed by read-back verification.

Qdrant ingestion is not started until the ServiceNow publication has been successfully verified.

This ordering prevents Qdrant from becoming the source of truth for an article that was not successfully persisted in ServiceNow.

## 7. Qdrant Ingestion

Knowledge capture reuses:

```text
src/retrieval/ingest.py
```

The existing deterministic point-ID contract is preserved.

The deterministic ID is derived from the article identity and chunk information, allowing repeated ingestion to remain idempotent.

The loop-closure test verifies that the generated Qdrant point ID is deterministic.

## 8. Failure and Inconsistency Handling

The system explicitly handles partial failure between ServiceNow and Qdrant.

### ServiceNow succeeds, Qdrant fails

The article remains persisted in ServiceNow.

The knowledge-capture audit records:

```text
status = partial_success
article_number
article_sys_id
qdrant_point_ids
error
execution_reference
```

The returned result identifies the consistency state as:

```text
servicenow_published_qdrant_sync_failed
```

This prevents the failure from becoming silent drift.

### ServiceNow verification fails

Qdrant ingestion is not started.

This ensures that only successfully verified ServiceNow articles enter the Qdrant synchronization path.

## 9. Audit Integration

Every knowledge write-back is associated with the original execution identifier.

The knowledge-capture audit records:

```text
execution_reference
article_number
article_sys_id
qdrant_point_ids
status
error
created_at
```

For successful synchronization:

```text
status = completed
```

For a ServiceNow-success/Qdrant-failure condition:

```text
status = partial_success
```

This creates an execution-to-knowledge trace:

```text
Execution ID
    |
    +-- ServiceNow Article ID
    |
    +-- Qdrant Point IDs
    |
    +-- Capture Status
    |
    +-- Error information when applicable
```

## 10. Tool Classification

The KB write-back tool is registered in:

```text
src/agent/tools/registry.py
```

as:

```text
kb_write_back
```

with:

```text
PermissionClass.HIGH_RISK
```

The tool therefore requires an approved and unconsumed approval before dispatch.

This classification is consistent with the existing permission model because KB write-back creates persistent external knowledge and therefore requires stronger authorization than read operations or ordinary low-risk writes.

## 11. Deterministic and Idempotent Behavior

The workflow uses the existing deterministic Qdrant point-ID contract.

Repeated ingestion of the same article/chunk does not create an unrelated point identity.

Approval consumption also prevents the same approval authorization from being reused indefinitely for protected tool execution.

Together these mechanisms reduce duplicate writes and uncontrolled replay.

## 12. Loop-Closure Demonstration and Verification

The Sprint 3.5 loop closure is demonstrated by:

```text
tests/test_loop_closure.py
```

The test exercises the complete knowledge-capture path:

```text
Incident Snapshot
      ↓
Human Resolution
      ↓
Article Composer
      ↓
Canonical Article
      ↓
ServiceNow Publication + Verification
      ↓
Qdrant Synchronization
      ↓
Deterministic Point ID
      ↓
Equivalent Incident Retrieval
      ↓
New Human-Resolution Article Returned
```

### Demonstration Scenario

The loop-closure test uses the following incident:

```text
Incident: INC-LOOP-001
Article:  KB-LOOP-001
Service:  email
```

Incident snapshot:

```text
Short description:
Email service unavailable

Description:
Users cannot access the email service.
```

Human-provided resolution:

```text
Restart the email service and verify that users can access email again.
```

The Article Composer produces a structured article containing:

```text
Title:
Email Service Recovery

Summary:
Users could not access the email service.

Steps:
1. Restart the email service.
2. Verify that users can access email again.
```

The resulting article is then passed through the existing ServiceNow publication path.

The test verifies the ServiceNow result:

```text
status     = created
article    = KB-LOOP-001
sys_id     = snow-loop-001
```

The article is then synchronized through the existing Qdrant ingestion path.

The test verifies that the Qdrant point ID is deterministic:

```text
deterministic_point_id(
    "KB-LOOP-001",
    1,
    0
)
```

matches the point ID generated by the ingestion process.

### Fresh Retrieval Verification

After ingestion, the test performs an equivalent-incident retrieval query:

```text
Users cannot access email service. Restart the email service.
```

The retrieval result is checked for the newly created article:

```python
assert article_number in returned_numbers
```

where:

```text
article_number = KB-LOOP-001
```

The retrieved article is additionally verified to contain:

```text
workflow_state = published
security_level = internal
```

Therefore, the automated loop-closure test demonstrates that the newly authored human-resolution article is persisted, ingested, and subsequently available as retrieval evidence for an equivalent incident.

### Verification Run

The focused Sprint 3.5 verification command was:

```powershell
pytest -q tests/test_article_composer.py tests/test_knowledge_capture.py tests/test_loop_closure.py
```

Recorded result:

```text
.........                                      [100%]
11 passed in 7.92s
```

This confirms that the Article Composer, knowledge-capture orchestration, and loop-closure retrieval test all pass together.

### Infrastructure Verification

Before the final verification run, the required infrastructure paths were also checked.

PostgreSQL:

```text
Postgres: OK
```

Qdrant:

```text
Qdrant URL configured: True
Qdrant collection: barq_knowledge_base
```

ServiceNow:

```text
ServiceNow URL configured: True
ServiceNow KB configured: True
ServiceNow OAuth configured: True
```

The existing integration paths were import-verified:

```text
ServiceNow publisher: OK
Qdrant ingestion: OK
Retrieval: OK
LangGraph resume: OK
```

### Evidence Boundary

The recorded `11 passed` result is an automated loop-closure demonstration using controlled test dependencies, including an in-memory Qdrant instance and mocked ServiceNow publication/source data.

It therefore demonstrates the complete S3.5 integration behavior and verifies the retrieval contract, but it should not be described as a production ServiceNow/Qdrant execution transcript.

A separate production demonstration transcript should only be added if the reviewer explicitly requires a live execution against the configured external ServiceNow and Qdrant environments.

## 13. Required Deliverables

The Sprint 3.5 implementation includes the required repository deliverables:

```text
src/agent/knowledge_capture.py
src/agent/article_composer.py
src/agent/prompts.py
src/api/schemas.py
src/retrieval/publish_kb.py
src/retrieval/ingest.py
src/agent/tools/registry.py
tests/test_knowledge_capture.py
tests/test_article_composer.py
tests/test_loop_closure.py
docs/sprint3_knowledge_capture_design.md
```

The focused verification suite passes:

```text
11 passed
```

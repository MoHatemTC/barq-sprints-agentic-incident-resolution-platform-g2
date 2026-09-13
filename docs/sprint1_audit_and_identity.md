# BARQ G2 — Sprint 1 (S1.2)
## AI Execution Log Table, OAuth Integration Identity & Least-Privilege ACLs

## 1. Overview

This document records the Sprint 1 implementation and empirical verification for the ServiceNow security and audit workstream of the **AI Incident Orchestrator** project.

### Application

- Application: `AI Incident Orchestrator`
- ServiceNow scope: `x_2216057_ai_inc_0`
- Platform: ServiceNow PDI
- Integration user: `barq_ai_integration`
- Integration role: `x_2216057_ai_inc_0.ai_integration`

### Sprint 1 scope

This sprint implements:

1. AI Execution Log audit table
2. OAuth integration identity
3. Purpose-built least-privilege role
4. Field-level ACL controls
5. Automated empirical permission verification
6. Documentation of OAuth token handling and permission decisions

---

# 2. AI Execution Log

## 2.1 Table

The scoped audit table is:

`x_2216057_ai_inc_0_ai_execution_log`

Display name:

`AI Execution Log`

The table does not extend Task.

## 2.2 Fields

| Field | Type | Mandatory | Purpose |
|---|---|---:|---|
| `incident` | Reference → Incident | Yes | Incident associated with the execution |
| `execution_id` | String | Yes | Groups records belonging to one orchestration execution |
| `action` | String | Yes | Graph node, tool call, approval, or orchestration action |
| `agent` | String | No | Agent or component that performed the action |
| `timestamp` | Date/Time | Yes | Time the audit event occurred |
| `status` | Choice | Yes | Result/state of the action |
| `result` | String | No | Successful result or returned information |
| `error` | String | No | Failure or exception information |

## 2.3 Status taxonomy

The following values are configured:

| Label | Value |
|---|---|
| Started | `started` |
| Succeeded | `succeeded` |
| Failed | `failed` |
| Blocked | `blocked` |
| Awaiting Approval | `awaiting_approval` |

## 2.4 Action taxonomy

The `action` field is intentionally a String field so downstream orchestration nodes and tools can use a predictable namespace.

Examples include:

- `orchestrator.start`
- `graph.classify_incident`
- `graph.generate_suggestion`
- `graph.resolve_incident`
- `tool.servicenow.read_incident`
- `tool.servicenow.update_incident`
- `tool.knowledge.search`
- `approval.request`

This allows the log schema to support future graph nodes and tool calls without requiring a new ServiceNow choice value for every action.

## 2.5 Execution ID indexing

A B-tree index is configured on:

`execution_id`

The index is non-unique because one orchestration execution can generate multiple audit records.

Example:

```text
execution_id = exec-123
orchestrator.start
graph.classify_incident
tool.servicenow.read_incident
graph.generate_suggestion
```

All records can be queried using the same execution ID.

## 2.6 Incident execution history

`AI Execution Log.incident` is a reference to the Incident table.

An `AI Execution Logs` related list is available from Incident records so execution history can be inspected from the affected incident.

The related list exposes:

- Timestamp
- Execution ID
- Action
- Agent
- Status
- Result
- Error

---

# 3. Integration Identity

## 3.1 Dedicated integration user

The ServiceNow integration identity is:

`barq_ai_integration`

The account is configured as a machine/service identity for API access.

Observed configuration includes:

- Active: Yes
- Identity type: Machine
- Web service access only: Yes
- Admin: No
- `security_admin`: No
- `itil_admin`: No

The user is not intended for normal interactive ServiceNow work.

## 3.2 Purpose-built role

The custom role is:

`x_2216057_ai_inc_0.ai_integration`

This role exists specifically to authorize the AI orchestration integration.

Broad administrator roles are not granted to the integration user.

A platform-provided/inherited script-writer-related permission was observed through ServiceNow group inheritance. It was not intentionally granted as part of the BARQ integration role and does not provide `admin`, `security_admin`, or `itil_admin`.

---

# 4. OAuth Configuration

## 4.1 OAuth application

An inbound OAuth integration is configured in ServiceNow for the AI Incident Orchestrator.

OAuth application name:

`BARQ AI Incident Orchestrator`

The Sprint 1 verification harness authenticates using a ServiceNow OAuth access token rather than an administrator session or administrator credentials.

The current verification flow uses the Resource Owner Password Credentials grant for the dedicated machine integration identity.

## 4.2 Secret handling

The following values must never be committed to source control:

- ServiceNow password
- OAuth client secret
- OAuth access token
- OAuth refresh token, if issued

Local runtime values are stored using environment variables.

Expected variables are:

```text
SERVICENOW_INSTANCE
SERVICENOW_USERNAME
SERVICENOW_PASSWORD
SERVICENOW_CLIENT_ID
SERVICENOW_CLIENT_SECRET
INCIDENT_SYS_ID
```

The repository contains `.env.example` with empty placeholders only.

The real `.env` file is excluded by `.gitignore`.

No administrator credentials are required by the verification harness.

---

# 5. OAuth Token Lifecycle

## 5.1 Token acquisition

Before performing Table API requests, the verification harness requests an OAuth access token from ServiceNow.

The token is then sent using:

```http
Authorization: Bearer <access_token>
```

The access token is held only for the runtime of the client process and must not be written to source files or documentation.

## 5.2 Expiration

OAuth access tokens are treated as temporary credentials.

The client must not assume that one token remains valid for an entire long-running orchestration.

The client should track expiration information returned by the OAuth endpoint where available.

## 5.3 Refresh behavior

If ServiceNow issues a refresh token, a production client should use the refresh token to obtain a new access token before or after access-token expiry.

If no usable refresh token is available for the configured grant, the client should reacquire an access token using the configured machine identity credentials.

Secrets must remain in an approved secret store or runtime environment and must not be embedded in source code.

## 5.4 Mid-run token expiry

If the access token expires during orchestration:

1. The API client detects an authentication failure such as HTTP `401`.
2. The current access token is discarded.
3. The client refreshes or reacquires an access token.
4. The failed request is retried once when it is safe to do so.
5. Repeated authentication failure is recorded as an execution failure.
6. The orchestration must not retry indefinitely.

For write operations, retry logic should preserve idempotency where possible to avoid duplicate audit or business records.

---

# 6. ACL Design

The integration identity is intentionally restricted.

Its purpose is to:

- read Incident information required by the orchestrator,
- write approved AI-specific fields,
- write execution audit records,
- write Work notes when required by the orchestration workflow.

It must not take ownership of normal human incident-management responsibilities.

## 6.1 AI fields configured for integration write access

The scoped application contains AI fields including:

- AI Processing State
- AI Classification
- AI Confidence
- AI Suggestion
- AI Resolution
- AI Failure Reason
- AI Model Name
- AI Agent Version
- AI Processing Started At
- AI Processing Ended At
- AI Human Review Required

The integration role is intended to update these orchestration-owned fields.

`AI Human Lock` is deliberately excluded from integration write access.

## 6.2 Human-controlled fields

The integration identity must not modify:

- `state`
- `assigned_to`
- `assignment_group`
- `priority`
- `comments`
- AI Human Lock

Deny protections were configured for these fields for the integration identity.

The intention is that operational decisions and the human-lock control remain owned by human users.

## 6.3 Work notes

The integration is intended to write:

`work_notes`

A dedicated Incident Work notes ACL exists for the integration role.

During testing, the inherited Task dictionary restriction for `work_notes` was also identified.

The Task `work_notes` dictionary write roles were updated to include:

```text
itil
task_editor
x_2216057_ai_inc_0.ai_integration
```

The automated verification harness still reports the Work notes verification as unresolved. See the Known Issue section below.

## 6.4 AI Execution Log create permission

The integration role has create access to the scoped AI Execution Log table.

This permission is required so every orchestration execution can produce a durable audit trail.

The integration identity is not granted an administrator role in order to create audit records.

---

# 7. Empirical Verification Harness

Verification is performed by:

`scripts/verify_permissions.py`

The script authenticates as:

`barq_ai_integration`

It performs actual Table API requests and compares the observed state before and after each operation.

The purpose is to verify effective permissions rather than relying only on ACL configuration inspection.

---

# 8. Empirical Permission Matrix

Latest verification result:

```text
Passed: 13
Failed: 1
Overall result: ATTENTION REQUIRED
```

## 8.1 Observed results

| Operation | Expected | Observed | Result |
|---|---|---|---|
| OAuth authentication | Allow | Access token obtained | PASS |
| Read Incident | Allow | HTTP 200 | PASS |
| Create AI Execution Log | Allow | HTTP 201 | PASS |
| Write AI Processing State | Allow | HTTP 200 / requested value retained | PASS |
| Write AI Classification | Allow | HTTP 200 / requested value retained | PASS |
| Write AI Model Name | Allow | HTTP 200 / requested value retained | PASS |
| Write AI Agent Version | Allow | HTTP 200 / requested value retained | PASS |
| Write AI Human Review Required | Allow | HTTP 200 / requested value retained | PASS |
| Write Work notes | Allow | HTTP 200, journal verification unresolved | OPEN |
| Write Priority | Deny | Value unchanged | PASS |
| Write State | Deny | Value unchanged | PASS |
| Write Assigned To | Deny | Value unchanged | PASS |
| Write Assignment Group | Deny | Value unchanged | PASS |
| Write AI Human Lock | Deny | Value unchanged | PASS |
| Write Comments | Deny | No journal entry observed | PASS |

---

# 9. Important HTTP Behavior

ServiceNow may return HTTP `200` for a PATCH even when a field-level security rule silently prevents a particular field from changing.

Therefore, the harness does not treat HTTP status alone as proof of authorization.

For denied fields it:

1. reads the original value,
2. attempts the modification,
3. reads the record again,
4. verifies that the protected value did not change.

This behavior is important for proving least privilege empirically.

Example:

```text
Priority before: 1
Attempted: 2
Priority after: 1
Result: PASS
```

The successful denial is demonstrated by the unchanged value rather than by requiring HTTP `403`.

---

# 10. Least-Privilege Reasoning

## 10.1 Incident read access

**Granted because:** the orchestrator requires incident context in order to classify the incident and produce AI recommendations.

Without Incident read access, the integration cannot perform its intended function.

## 10.2 AI field write access

**Granted because:** these fields contain state and metadata produced by the AI orchestration system itself.

Examples include:

- classification,
- model name,
- processing state,
- agent version,
- review-required flag.

These fields are integration-owned rather than normal human incident-management fields.

## 10.3 AI Execution Log create access

**Granted because:** every orchestration execution must be auditable.

The integration identity must be capable of recording actions, statuses, results, and failures without using administrator credentials.

## 10.4 Work notes access

**Granted because:** the orchestrator may need to add internal diagnostic or AI-generated context to an Incident without posting customer-visible comments.

Final journal-level verification remains open at the time of this document.

## 10.5 State

**Denied because:** changing Incident state is an operational decision that should not occur implicitly through the integration identity.

## 10.6 Assigned To

**Denied because:** assignment of an Incident to an individual is a human/service-management responsibility.

## 10.7 Assignment Group

**Denied because:** routing responsibility must remain under approved ServiceNow process controls.

## 10.8 Priority

**Denied because:** priority directly affects operational response and SLA behavior and must not be changed by the AI integration.

## 10.9 Comments

**Denied because:** Comments can be customer-facing.

The AI integration is intentionally prevented from posting directly to this channel.

## 10.10 AI Human Lock

**Denied because:** the Human Lock is specifically intended to give humans authority to stop or constrain AI-driven automation.

Allowing the integration identity to change its own lock would defeat the governance purpose of the field.

---

# 11. Human Lock Security Property

The AI Human Lock field is intentionally human-controlled.

The integration identity must not be able to:

- enable it,
- disable it,
- clear a human-set lock,
- override a human decision.

Empirical verification attempted to change the field and confirmed the stored value remained unchanged.

This establishes separation between the automated actor and the human governance control.

---

# 12. Current Known Issue — Work Notes Verification

The only remaining failing automated test is:

```text
Allowed work_notes
```

Observed behavior:

```text
PATCH HTTP status: 200
Journal count before: 0
Journal count after: 0
Result: FAIL
```

The Work notes ACL and Task dictionary write-role configuration have been updated to authorize the integration role.

However, the current verification harness checks journal records and has not yet proven that the expected Work notes journal entry was created.

One possible explanation is that the integration identity may be able to write Work notes while lacking permission to read the underlying journal records used by the verification query.

This must be verified separately before declaring the Work notes requirement fully complete.

No additional broad read permission should be granted only to make a test pass unless that permission is genuinely required by the production integration.

Current status:

```text
13 of 14 permission checks passing
Work notes verification pending
```

---

# 13. Security Decisions

The implementation follows these security principles:

1. Use a dedicated machine integration identity.
2. Do not use an administrator account for API operations.
3. Use a purpose-built scoped integration role.
4. Grant only permissions required for orchestration.
5. Explicitly protect human-owned fields.
6. Protect the Human Lock from the automated identity.
7. Store secrets outside source control.
8. Verify effective behavior with actual API operations.
9. Audit orchestration actions through the AI Execution Log.
10. Record unresolved verification honestly rather than assuming configuration is effective.

---

# 14. Secrets and Repository Hygiene

The following files are used for local configuration:

```text
.env
.env.example
```

The real `.env` file is ignored by Git.

`.env.example` contains only variable names and empty placeholders.

Example:

```env
SERVICENOW_INSTANCE=
SERVICENOW_USERNAME=
SERVICENOW_PASSWORD=
SERVICENOW_CLIENT_ID=
SERVICENOW_CLIENT_SECRET=
INCIDENT_SYS_ID=
```

The following must not appear in commits:

- actual ServiceNow password
- OAuth client secret
- OAuth access token
- refresh token
- administrator credentials

---

# 15. Verification Execution

The automated test can be run from the repository root with:

```powershell
py scripts/verify_permissions.py
```

The local environment must contain the required OAuth and ServiceNow environment variables before execution.

The verification script tests both successful and refused operations.

---

# 16. ServiceNow Update Set Deliverable

The scoped application was published to a ServiceNow Update Set.

The published update set contains the scoped application configuration, including the AI Execution Log table and associated application records.

Repository location:

```text
servicenow/
└── ai_incident_orchestrator/
    └── AI_Incident_Orchestrator_Sprint1.xml
```

The published update set was exported without demo data.

---

# 17. Sprint Deliverables

The Sprint 1 deliverables are:

```text
servicenow/
└── ai_incident_orchestrator/
    └── AI_Incident_Orchestrator_Sprint1.xml

docs/
└── sprint1_audit_and_identity.md

scripts/
└── verify_permissions.py

.env.example
.gitignore
```

---

# 18. Current Sprint Status

## Completed

- AI Execution Log table created
- Required execution-log fields created
- Execution ID indexed
- Status taxonomy configured
- Action naming strategy defined
- Incident execution history related list configured
- Dedicated machine integration user created
- Purpose-built integration role created
- OAuth application configured
- OAuth authentication verified
- Incident read verified
- Execution Log creation verified
- Approved AI field writes verified for the tested fields
- Priority write refused
- State write refused
- Assigned To write refused
- Assignment Group write refused
- AI Human Lock write refused
- Comments write refused
- ServiceNow Update Set published and exported
- Automated permission verification implemented

## Pending verification

- Work notes journal-write verification

Latest automated result:

```text
Passed: 13
Failed: 1
```

The outstanding Work notes verification is documented rather than hidden or reported as complete.

---

# 19. Definition-of-Done Evidence

The Sprint success criteria require:

> an execution log record can be written through the Table API by the integration identity, write attempts to restricted fields are proven denied, and no admin credentials exist anywhere in configuration, code, templates, or documentation.

Current evidence:

- Execution Log Table API create: **PASS — HTTP 201**
- Restricted field denial tests: **PASS**
- OAuth machine identity authentication: **PASS**
- Administrator role required by integration: **NO**
- Real secrets intended for repository storage: **NO**
- Work notes: **pending final journal verification**

The core audit-trail and least-privilege controls are therefore functioning, with one explicitly documented verification item remaining.

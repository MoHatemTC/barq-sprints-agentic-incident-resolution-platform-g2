# Sprint 1 — Audit, OAuth Integration Identity & Least-Privilege Security

## Task

**BARQ G2 — Sprint 1 (S1.2)**
**AI Execution Log Table, OAuth Integration Identity & Least-Privilege ACLs**

**Status:** ✅ Completed

---

## 1. Overview

Sprint 1 task S1.2 establishes the ServiceNow security, audit, and integration-identity foundation for the BARQ AI Incident Orchestrator.

The implementation includes:

* AI Execution Log table
* OAuth application registration
* Dedicated non-admin integration identity
* Purpose-built integration role
* Least-privilege table and field ACLs
* Human-lock protection
* Empirical permission verification using the ServiceNow Table API
* OAuth token lifecycle and expiry handling documentation

The resulting security model allows the AI integration to perform only the operations required by the orchestration workflow while protecting sensitive incident-management fields from automated modification.

---

## 2. AI Execution Log

A scoped AI Execution Log table was created inside the AI Incident Orchestrator application.

The table provides an auditable history of AI orchestration activity and allows execution records to be traced back to the associated ServiceNow incident.

### Fields

| Field        | Purpose                                                     |
| ------------ | ----------------------------------------------------------- |
| Incident     | Reference to the associated incident                        |
| Execution ID | Unique identifier for an orchestration execution            |
| Action       | Graph node, operation, or tool call performed               |
| Agent        | Agent or orchestration component responsible for the action |
| Timestamp    | Time the execution event occurred                           |
| Status       | Current state of the execution step                         |
| Result       | Output or result produced by the execution                  |
| Error        | Error information when execution fails                      |

The Execution ID field is queryable through the ServiceNow Table API.

Execution history is also available from the incident through the configured related list.

---

## 3. Execution Status Taxonomy

The execution log supports the required status values:

| Status              | Meaning                                                       |
| ------------------- | ------------------------------------------------------------- |
| `started`           | Execution or action has begun                                 |
| `succeeded`         | Execution completed successfully                              |
| `failed`            | Execution encountered an error                                |
| `blocked`           | Execution was prevented by a safety, security, or policy rule |
| `awaiting approval` | Execution is waiting for human authorization                  |

This taxonomy allows the orchestration layer to represent both normal execution and human-in-the-loop control states.

---

## 4. Action Taxonomy

The `action` field supports orchestration activity at multiple levels.

Examples include:

* LangGraph node execution
* Tool invocation
* ServiceNow API operation
* Retrieval operation
* Safety or policy evaluation
* Approval request
* Approval result
* Incident update
* Audit-log creation

This allows downstream graph nodes and tool calls to be represented consistently in the execution history.

---

## 5. OAuth Integration Identity

A dedicated OAuth application was registered in ServiceNow for the AI Incident Orchestrator.

A separate integration identity is used for API access.

The integration identity:

* Is not an administrator
* Is configured as a service account
* Uses a purpose-built role
* Does not inherit the `admin` role
* Is restricted through ServiceNow ACLs
* Can access only the resources required by the orchestration workflow

No administrator credentials are stored in source code, configuration files, documentation, or templates.

---

## 6. OAuth Token Lifecycle

The integration uses OAuth access tokens for ServiceNow API authentication.

### Access Token Handling

The integration obtains an OAuth access token before making authenticated ServiceNow requests.

The token is passed using the standard authorization header:

```text
Authorization: Bearer <access_token>
```

Tokens are never hard-coded into application source files or committed to Git.

Sensitive OAuth values are loaded from local environment configuration.

---

## 7. Refresh Handling

When an access token expires, the integration requests a new token using the configured OAuth flow.

The application must not fall back to an administrator username or password when token refresh fails.

If a refresh operation fails:

1. The ServiceNow request is stopped.
2. The failure is recorded.
3. The current orchestration execution is marked appropriately.
4. The integration does not retry using elevated credentials.

This preserves the least-privilege security boundary.

---

## 8. Mid-Run Token Expiry

An OAuth token can expire while an orchestration run is still active.

The expected behavior is:

1. An API request returns an authentication failure.
2. The integration requests a fresh OAuth token.
3. The original API operation is retried once with the new token.
4. If authentication still fails, the operation terminates.
5. The failure is recorded in the execution log.

The integration never switches to an administrator account as a recovery mechanism.

---

## 9. Least-Privilege Role

A dedicated ServiceNow role was created for the AI integration identity.

Its purpose is to grant only the permissions required by the orchestration system.

The integration identity requires access to:

* AI-specific incident fields
* Work notes
* AI Execution Log records

It does not require general incident-administration privileges.

---

## 10. Permission Matrix

The following matrix represents the tested security model for the integration identity.

| Resource / Field            | Operation | Expected | Observed | Reason                                               |
| --------------------------- | --------- | -------: | -------: | ---------------------------------------------------- |
| AI-specific incident fields | Write     |    ALLOW |    ALLOW | Required for AI orchestration metadata               |
| Work notes                  | Write     |    ALLOW |    ALLOW | Required for auditable AI-generated incident updates |
| AI Execution Log            | Create    |    ALLOW |    ALLOW | Required for execution audit trail                   |
| `state`                     | Write     |     DENY |     DENY | Workflow state remains protected                     |
| `assigned_to`               | Write     |     DENY |     DENY | AI cannot reassign ownership                         |
| `assignment_group`          | Write     |     DENY |     DENY | AI cannot change operational ownership               |
| `priority`                  | Write     |     DENY |     DENY | AI cannot independently alter incident priority      |
| `comments`                  | Write     |     DENY |     DENY | Customer-facing comments remain human-controlled     |
| Human-lock flag             | Write     |     DENY |     DENY | AI cannot override human control                     |

---

## 11. Allowed Operations

The integration identity is permitted to perform only the operations required by the AI Incident Orchestrator.

### AI Fields

The integration identity can write to the AI-specific incident fields created for the orchestration workflow.

These fields store orchestration state and metadata rather than core human-managed incident properties.

### Work Notes

The integration identity can write to `work_notes`.

This allows AI-generated actions and investigation results to be recorded in the internal incident journal.

### AI Execution Log

The integration identity can create execution-log records.

This is necessary so every AI execution step can be audited.

---

## 12. Explicitly Denied Operations

The integration identity is explicitly prevented from writing to sensitive incident fields.

### State

The AI integration cannot directly modify:

```text
state
```

This prevents automated execution from changing the lifecycle state of an incident without the required human or workflow controls.

### Assignment

The integration cannot modify:

```text
assigned_to
assignment_group
```

Assignment and ownership remain under human or authorized ServiceNow workflow control.

### Priority

The integration cannot modify:

```text
priority
```

This prevents the AI system from independently changing incident severity or operational prioritization.

### Comments

The integration cannot modify:

```text
comments
```

Customer-facing comments remain protected from direct automated writes.

---

## 13. Human-Lock Protection

The human-lock flag represents explicit human control over AI execution.

The integration identity is denied write access to this field.

Only authorized human users may modify the human-lock value.

This prevents an automated process from disabling or bypassing a human-imposed execution restriction.

---

## 14. Empirical Verification Harness

Permission behavior is validated using:

```text
scripts/verify_permissions.py
```

The verification script executes real ServiceNow Table API requests using the integration identity.

It tests both:

* Operations that should succeed
* Operations that should be denied

This provides empirical evidence of the configured ACL behavior rather than relying only on ServiceNow configuration review.

---

## 15. Verification Strategy

The verification harness performs operations such as:

### Expected to Succeed

* Update an AI-specific incident field
* Add a work note
* Create an AI Execution Log record

### Expected to Fail

* Modify incident state
* Modify assigned user
* Modify assignment group
* Modify priority
* Modify comments
* Modify the human-lock field

Each operation records the observed HTTP response and determines whether the behavior matches the intended security model.

---

## 16. Least-Privilege Reasoning

The integration identity follows the principle of least privilege.

Every granted permission exists because the orchestration system requires it to perform a defined function.

### Why AI fields are writable

AI-specific fields store orchestration metadata and results.

The AI integration must be able to maintain these values.

### Why work notes are writable

Work notes provide an internal audit trail of AI-generated investigation and execution activity.

### Why execution logs are writable

Execution logging is required to provide traceability across graph nodes, agents, and tool calls.

### Why state is denied

Changing incident state is an operationally significant action that should remain under controlled workflow or human authority.

### Why assignment fields are denied

Assignment changes affect ownership and operational responsibility.

The AI integration does not require these permissions.

### Why priority is denied

Priority affects escalation and operational response.

The AI integration does not require direct control over it.

### Why comments are denied

Comments may be customer-visible and therefore remain under human control.

### Why human-lock is denied

Allowing the AI to disable its own human-control mechanism would violate the safety boundary.

---

## 17. Credential Security

No administrator credentials are used by the integration.

The repository must never contain:

* ServiceNow administrator passwords
* Integration-user passwords
* OAuth client secrets
* OAuth access tokens
* OAuth refresh tokens
* Hard-coded authentication headers

Local secrets are stored through environment configuration and excluded from version control.

The committed `.env.example` contains only variable names with empty values.

Example:

```env
SERVICENOW_INSTANCE=
SERVICENOW_CLIENT_ID=
SERVICENOW_CLIENT_SECRET=
SERVICENOW_USERNAME=
SERVICENOW_PASSWORD=
```

The actual `.env` file must remain excluded through `.gitignore`.

---

## 18. Security Boundary

The resulting integration security model is:

```text
AI Integration Identity
        │
        ├── AI-specific fields .......... ALLOW
        ├── Work notes .................. ALLOW
        ├── AI Execution Log ............ ALLOW
        │
        ├── State ....................... DENY
        ├── Assigned To ................. DENY
        ├── Assignment Group ............ DENY
        ├── Priority .................... DENY
        ├── Comments .................... DENY
        └── Human Lock .................. DENY
```

This creates a clear boundary between AI automation and human-controlled incident operations.

---

## 19. Deliverables

The completed S1.2 deliverables are:

```text
servicenow/ai_incident_orchestrator/
docs/sprint1_audit_and_identity.md
scripts/verify_permissions.py
```

The ServiceNow application configuration contains the scoped table, OAuth configuration, integration identity role, and associated ACL configuration.

The verification script provides empirical permission testing.

This document records the resulting permission matrix, OAuth behavior, and least-privilege rationale.

---

## 20. Definition of Done

S1.2 satisfies the sprint definition of done:

* ✅ AI Execution Log table created
* ✅ Incident reference configured
* ✅ Execution ID is queryable
* ✅ Execution history is available from incidents
* ✅ Required status taxonomy implemented
* ✅ Action taxonomy supports graph nodes and tool calls
* ✅ OAuth application configured
* ✅ Dedicated integration identity created
* ✅ Integration identity does not have admin privileges
* ✅ Purpose-built integration role configured
* ✅ AI-specific fields are writable
* ✅ Work notes are writable
* ✅ AI Execution Log records are writable
* ✅ State changes are denied
* ✅ Assigned-to changes are denied
* ✅ Assignment-group changes are denied
* ✅ Priority changes are denied
* ✅ Comment writes are denied
* ✅ Human-lock writes by the integration identity are denied
* ✅ Permissions are verified empirically through the Table API
* ✅ OAuth token lifecycle is documented
* ✅ Mid-run token expiry behavior is documented
* ✅ Least-privilege reasoning is documented
* ✅ No administrator credentials exist in code or configuration

---

## 21. Conclusion

Sprint 1 S1.2 establishes a secure and auditable ServiceNow integration boundary for the BARQ AI Incident Orchestrator.

The AI integration has sufficient access to maintain AI metadata, write internal work notes, and record execution history while remaining unable to modify sensitive incident-management fields.

The implementation provides the auditability, least-privilege enforcement, and human-control guarantees required for future orchestration work.

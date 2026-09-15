# BARQ — Agentic Incident Resolution Platform G2

A production-grade ServiceNow incident resolution platform featuring LangGraph orchestration, hybrid RAG, safety guardrails, observability, least-privilege access control, and human-in-the-loop approval.

**Current sprint:** Sprint 1
**S1.2 status:**  Completed — AI Execution Log, OAuth Integration Identity & Least-Privilege ACLs



---

## S1.2 — Security, Audit & Integration Identity

Sprint 1 task S1.2 establishes the ServiceNow audit trail and restricted integration identity used by the AI Incident Orchestrator.

The implementation follows a least-privilege model: the AI integration identity can perform only the operations required by the orchestration platform while sensitive incident-management fields remain protected.

### AI Execution Log

A scoped **AI Execution Log** table was created in the AI Incident Orchestrator ServiceNow application.

Execution records capture:

* Incident reference
* Execution ID
* Action
* Agent
* Timestamp
* Status
* Result
* Error

Execution IDs are queryable, and execution history is available from the associated incident.

The execution status taxonomy supports at least:

* `started`
* `succeeded`
* `failed`
* `blocked`
* `awaiting approval`

The action model supports orchestration activity including downstream graph nodes and tool calls.

---

## OAuth Integration Identity

A dedicated ServiceNow OAuth application and integration identity are used for machine-to-machine access.

The integration user:

* Is a dedicated service account
* Does **not** have the `admin` role
* Uses a purpose-built integration role
* Authenticates using OAuth rather than administrator credentials
* Is restricted by ServiceNow ACLs
* Can write audit events to the AI Execution Log

OAuth token lifecycle behavior, refresh handling, and mid-run token expiry behavior are documented in:

```text
docs/sprint1_audit_and_identity.md
```

No administrator credentials, OAuth secrets, passwords, or access tokens are committed to the repository.

---

## Least-Privilege ACL Model

ServiceNow ACLs restrict the integration identity to the fields required by the AI orchestration workflow.

### Permitted Operations

The integration identity can write:

* AI-specific incident fields
* Work notes
* AI Execution Log records

### Explicitly Restricted Incident Fields

The integration identity is denied write access to:

* `state`
* `assigned_to`
* `assignment_group`
* `priority`
* `comments`

The human-lock field is also protected from the integration identity and remains controlled by human users.

This prevents the AI orchestration layer from silently changing ownership, workflow state, priority, human comments, or human-control safeguards.

---

## Empirical Permission Verification

Permissions are verified through the ServiceNow Table API rather than being assumed from ACL configuration.

The verification harness is located at:

```text
scripts/verify_permissions.py
```

It executes both permitted and prohibited operations and records the observed ServiceNow responses.

The harness verifies that:

* AI fields can be written
* Work notes can be written
* AI Execution Log records can be created
* Restricted fields cannot be modified
* Human-lock modification by the integration identity is rejected

Run the verification harness with:

```bash
python scripts/verify_permissions.py
```

Observed results and least-privilege reasoning are documented in:

```text
docs/sprint1_audit_and_identity.md
```

---

## Project Structure

```text
barq-sprints-agentic-incident-resolution-platform-g2/
│
├── docker-compose.yml                    # S1.4 — Qdrant + PostgreSQL + Redis
├── .env.example                          # committed with empty values only
├── .gitignore                            # .env and local secrets excluded
├── README.md
├── requirements.txt
│
├── servicenow/
│   └── ai_incident_orchestrator/
│       ├── .gitkeep
│       └── *.xml                         # S1.1/S1.2/S1.3 ServiceNow update sets
│
├── src/
│   ├── __init__.py
│   │
│   ├── servicenow/                       # S1.5 — Table API client
│   │   └── __init__.py
│   │
│   └── retrieval/                        # S1.4
│       └── __init__.py
│
├── scripts/
│   └── verify_permissions.py             # S1.2 — empirical ACL verification
│
├── data/
│   └── coverage_matrix.csv               # S1.4 — retrieval ground truth
│
├── tests/
│   └── __init__.py                       # S1.5
│
└── docs/
    ├── sprint1_audit_and_identity.md      # S1.2 — ACL matrix + OAuth lifecycle
    └── sprint1_servicenow_client.md       # S1.5
```

---

## S1.2 Deliverables

The completed S1.2 implementation contains the following deliverables:

```text
servicenow/ai_incident_orchestrator/
docs/sprint1_audit_and_identity.md
scripts/verify_permissions.py
```

### ServiceNow Configuration

`servicenow/ai_incident_orchestrator/` contains the ServiceNow scoped-application changes associated with:

* AI Execution Log table
* Execution log field model
* Incident execution-history relationship
* OAuth integration setup
* Dedicated integration role
* Table ACLs
* Field-level ACLs
* Human-lock protection

### Audit & Identity Documentation

`docs/sprint1_audit_and_identity.md` documents:

* OAuth integration identity
* Token lifecycle
* Refresh behavior
* Mid-run token expiry handling
* Permission matrix
* Observed Table API results
* Granted permissions
* Denied permissions
* Least-privilege reasoning

### Permission Test Harness

`scripts/verify_permissions.py` validates ServiceNow permissions empirically using the Table API.

The script is intended to demonstrate that expected writes succeed and restricted writes fail under the integration identity.

---

## Security Requirements

The repository follows the following credential-handling rules:

* Never commit `.env`
* Never commit ServiceNow passwords
* Never commit OAuth client secrets
* Never commit access tokens
* Never commit refresh tokens
* Never use an administrator account for the AI integration
* Never place credentials directly inside Python source files
* Never place credentials inside ServiceNow configuration documentation

Only environment-variable names and empty example values belong in `.env.example`.

Example:

```env
SERVICENOW_INSTANCE=
SERVICENOW_CLIENT_ID=
SERVICENOW_CLIENT_SECRET=
SERVICENOW_USERNAME=
SERVICENOW_PASSWORD=
```

Local secrets must be stored only in:

```text
.env
```

and `.env` must remain ignored by Git.

---

## Setup

### Prerequisites

* Python 3.11+
* Git
* Docker / Docker Compose for S1.4 infrastructure
* Access to the configured ServiceNow PDI

### 1. Clone the Repository

```bash
git clone https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g2.git
cd barq-sprints-agentic-incident-resolution-platform-g2
```

### 2. Create and Activate a Virtual Environment

```bash
python -m venv venv
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the environment template and fill in the values locally.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS / Linux:

```bash
cp .env.example .env
```

`.env` must never be committed.

---

## Start the Infrastructure Stack

S1.4 infrastructure uses Qdrant, PostgreSQL, and Redis.

Start the stack:

```bash
docker compose up -d
```

Check container health:

```bash
docker compose ps
```

Stop the stack:

```bash
docker compose down
```

---

## Verification

### Verify ServiceNow Permissions

Run the S1.2 empirical permission harness:

```bash
python scripts/verify_permissions.py
```

The expected security model is:

```text
Integration Identity
        │
        ├── AI fields ................ ALLOW
        ├── Work notes ............... ALLOW
        ├── AI Execution Log ......... ALLOW
        │
        ├── State .................... DENY
        ├── Assigned To .............. DENY
        ├── Assignment Group ......... DENY
        ├── Priority ................. DENY
        ├── Comments ................. DENY
        └── Human Lock ............... DENY
```

### Run Automated Tests

```bash
pytest
```

---

## S1.2 Definition of Done

S1.2 is considered complete because:

* ✅ AI Execution Log table exists in the scoped ServiceNow application
* ✅ Execution records can be associated with incidents
* ✅ Execution IDs are queryable
* ✅ Execution history is available from incidents
* ✅ Required execution statuses are supported
* ✅ OAuth integration application is configured
* ✅ Dedicated non-admin service identity exists
* ✅ Purpose-built integration role exists
* ✅ Required AI fields are writable
* ✅ Work notes are writable
* ✅ Execution log records are writable
* ✅ `state` writes are denied
* ✅ `assigned_to` writes are denied
* ✅ `assignment_group` writes are denied
* ✅ `priority` writes are denied
* ✅ `comments` writes are denied
* ✅ Human-lock writes from the integration identity are denied
* ✅ Permissions are tested empirically through the Table API
* ✅ Permission results and least-privilege reasoning are documented
* ✅ No administrator credentials are stored in code or configuration

---

## Contributing

### Dependencies

Whenever a new Python package is installed, regenerate `requirements.txt`:

```bash
pip install <package>
pip freeze > requirements.txt
```

Commit the updated `requirements.txt` together with the change that requires it.

After pulling changes that modify dependencies:

```bash
pip install -r requirements.txt
```

---

## Branching & Pull Requests

Do **not** commit directly to `main`.

Every change must go through a feature branch and pull request.

Create a branch:

```bash
git checkout main
git pull origin main
git checkout -b <your-branch-name>
```

Commit and push:

```bash
git add .
git commit -m "<type>: <short description>"
git push -u origin <your-branch-name>
```

Then:

1. Open a pull request against `main`.
2. Request review.
3. Address review comments.
4. Merge only after approval.

For S1.2, the working branch is:

```text
feature/s1-2-execution-log-oauth-acl
```

---

## Architecture Principles

The BARQ AI Incident Orchestrator follows several core principles:

**Least privilege** — AI identities receive only the permissions necessary to perform their intended operations.

**Auditable execution** — orchestration actions are recorded through the AI Execution Log.

**Human control** — sensitive workflow decisions and human-lock controls cannot be overridden by the integration identity.

**Credential isolation** — secrets remain outside source control.

**Empirical security verification** — permissions are validated through actual API requests rather than inferred solely from configuration.

**Separation of responsibilities** — ServiceNow security, orchestration, retrieval, observability, and infrastructure concerns remain independently testable.

---


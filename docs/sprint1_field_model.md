# Sprint 1 — Incident Field Model & Data Dictionary

**Application:** AI Incident Orchestrator  
**Scope:** `x_2215689_ai_inc_0`  
**Table:** Incident (`incident`)  
**Update Set File:** `servicenow/ai_incident_orchestrator/S1.1_field_model.xml`  

---

## 1. Field Groups & Data Dictionary

The fields on the `incident` table are organized into 5 logical groups:

### Group 1: AI Processing & Retry Lifecycle
| Field Label | Column Name | Type | Permitted Values / Range | Default | Who Writes It | Write Permission | Purpose |
|---|---|---|---|---|---|---|---|
| **AI Processing State** | `x_2215689_ai_inc_0_u_ai_processing_state` | Choice | `pending`, `in_progress`, `awaiting_approval`, `waiting_for_retry`, `complete`, `failed` | `pending` | Business Rule / Backend (Celery & LangGraph) | Integration User / Admin (Read-only for agents) | Tracks the current processing state of the incident. |
| **AI Processing Start** | `x_2215689_ai_inc_0_ai_processing_start` | Date/Time | Valid Date/Time | — | Backend (on webhook receipt) | Integration User (Read-only for agents) | Timestamp when AI processing started. |
| **AI Processing End** | `x_2215689_ai_inc_0_ai_processing_end` | Date/Time | Valid Date/Time (>= Start) | — | Backend (on completion/failure) | Integration User (Read-only for agents) | Timestamp when AI processing finished. Must be after or equal to Start. |
| **AI Retry Count** | `x_2215689_ai_inc_0_ai_retry_count` | Integer | `>= 0` | `0` | Business Rule / Backend | Integration User / System | Current number of retry attempts executed for this incident. |
| **AI Max Retries** | `x_2215689_ai_inc_0_ai_max_retries` | Integer | Positive integer | `3` | Admin / Backend | Admin / Integration User | Maximum allowable retry attempts before permanently marking as `failed`. |
| **AI Retry Time Out** | `x_2215689_ai_inc_0_ai_retry_time_out` | Date/Time | Valid Future Date/Time | — | Business Rule (`AI Schedule Retry`) | Integration User / System | Scheduled timestamp for the next retry attempt (backoff window). |

### Group 2: AI Classification
| Field Label | Column Name | Type | Permitted Values / Range | Default | Who Writes It | Write Permission | Purpose |
|---|---|---|---|---|---|---|---|
| **AI Classification** | `x_2215689_ai_inc_0_u_ai_classification` | String (100) | Valid category (e.g. Network, Database, Software) | — | Backend (`classify` node) | Integration User (Read-only for agents) | The category predicted by the AI. |
| **AI Confidence** | `x_2215689_ai_inc_0_ai_confidence` | Decimal | `0.00` to `1.00` | — | Backend (`confidence` node) | Integration User (Read-only for agents) | Confidence score of the AI output (validated by business rule). |

### Group 3: AI Resolution
| Field Label | Column Name | Type | Permitted Values / Range | Default | Who Writes It | Write Permission | Purpose |
|---|---|---|---|---|---|---|---|
| **AI Suggestion** | `x_2215689_ai_inc_0_ai_suggestion` | String (4000) | Text / Markdown recommendation | — | Backend (`generate` node) | Integration User (Read-only for agents) | The AI's suggested diagnosis and remediation steps. |
| **AI Resolution** | `x_2215689_ai_inc_0_ai_resolution` | String (4000) | Text / Markdown resolution | — | Backend / Incident Agent | Integration User & Incident Agent | The actual resolution applied or approved for the incident. |
| **AI Failure Reason** | `x_2215689_ai_inc_0_ai_failure_reason` | String (4000) | Free text / error message | — | Backend (error/guardrails) | Integration User (Read-only for agents) | Reason why processing failed (required by business rule if state is Failed). |

### Group 4: AI Metadata
| Field Label | Column Name | Type | Permitted Values / Range | Default | Who Writes It | Write Permission | Purpose |
|---|---|---|---|---|---|---|---|
| **AI Model Name** | `x_2215689_ai_inc_0_ai_model_name` | String (100) | Model identifier (e.g. gemini-1.5-pro) | — | Backend | Integration User (Read-only for agents) | Name of the model used for reasoning. |
| **AI Agent Version** | `x_2215689_ai_inc_0_ai_agent_version` | String (100) | Version string (e.g. v1.0.0) | — | Backend | Integration User (Read-only for agents) | Version of the backend agent code. |

### Group 5: Human Governance & Eligibility
| Field Label | Column Name | Type | Permitted Values / Range | Default | Who Writes It | Write Permission | Purpose |
|---|---|---|---|---|---|---|---|
| **AI Enabled** | `x_2215689_ai_inc_0_ai_enabled` | True/False | `true` / `false` | `true` | Admin / Incident Agent | Admin / Incident Agent | Master toggle enabling AI evaluation for this incident (Opt-out by default). |
| **Human Review Required** | `x_2215689_ai_inc_0_human_review_required` | True/False | `true` / `false` | `false` | Backend | Integration User & Incident Agent | Indicates an active requirement for human intervention before proceeding. |
| **Human Lock** | `x_2215689_ai_inc_0_human_lock` | True/False | `true` / `false` | `false` | Incident Agent (Manual) | Incident Agent (Backend cannot edit) | When checked, AI processing is completely suppressed on this incident. |

---

## 2. Why Suggestion and Resolution are Distinct Fields

`AI Suggestion` and `AI Resolution` remain separate fields because they represent completely different lifecycle stages and have different audit meanings:

1. **Proposed Action vs. Final Outcome:**
   - A **Suggestion** is an unverified recommendation produced by the AI model during reasoning.
   - A **Resolution** is the official, verified outcome recorded after the workflow has been completed, approved, or manually resolved.

2. **Preventing Accidental Assumptions:**
   - Keeping them separate prevents an incident agent from mistaking an AI recommendation for an already-executed production change. 

3. **Human-in-the-Loop & Quality Evaluation:**
   - It allows the system to preserve the AI's original recommendation while recording the final human-approved or human-edited result. In later sprints, comparing `AI Suggestion` against `AI Resolution` lets us evaluate recommendation accuracy, edit distance, and hallucination rates.

---

## 3. Processing State Lifecycle

The `AI Processing State` field uses 6 choices:

- **`pending`**: The incident is new or updated, eligible, and waiting for backend pickup.
- **`in_progress`**: The backend Celery worker is currently processing the incident through LangGraph.
- **`awaiting_approval`**: The agent encountered a high-risk action or low confidence and paused for human sign-off.
- **`waiting_for_retry`**: The run encountered a temporary failure; a retry has been scheduled under exponential backoff.
- **`complete`**: The run finished successfully and the resolution/suggestions were saved.
- **`failed`**: The run encountered an error or exceeded maximum retries. `AI Failure Reason` explains why.

---

## 4. Form Layout

The fields are arranged on the Incident form under dedicated sections matching the 5 logical groups:

```text
Incident Form
│
├── Human Governance (Top Header)
│   ├── AI Enabled
│   ├── Human Lock
│   ├── Human Review Required
│   └── AI Execution Log (Embedded List)
│
└── Tabbed Sections
    │
    ├── 1. AI Processing
    │   ├── AI Processing State
    │   ├── AI Processing Start
    │   ├── AI Processing End
    │   ├── AI Max Retries
    │   ├── AI Retry Count
    │   └── AI Retry Time Out
    │
    ├── 2. AI Classification
    │   ├── AI Classification
    │   └── AI Confidence
    │
    ├── 3. AI Resolution
    │   ├── AI Suggestion
    │   ├── AI Resolution
    │   └── AI Failure Reason
    │
    └── 4. AI Metadata
        ├── AI Model Name
        └── AI Agent Version
```

---

## 5. Validation & Lifecycle Business Rules

The scoped application includes 6 Business Rules executing on `incident` (before insert / update):

### Data Validation Rules:
1. **Validate AI Confidence:** Ensures `AI Confidence` is a valid decimal between `0.0` and `1.0` (executes only when `AI Confidence` changes).
2. **validate start_end:** Ensures `AI Processing End` is chronologically after `AI Processing Start` (executes only when start or end timestamps change).
3. **Enforce AI Processing State:** Prevents setting state to `failed` if `AI Failure Reason` is empty.  
   > ⚠️ **Integration Note for S1.5:** When setting `AI Processing State` to `failed`, S1.5 must pass `x_2215689_ai_inc_0_u_ai_processing_state: 'failed'` and `x_2215689_ai_inc_0_ai_failure_reason` together in a single PATCH request.

### Retry & Governance Lifecycle Rules:
4. **AI Schedule Retry:** If state transitions to `failed` (`current.x_2215689_ai_inc_0_u_ai_processing_state.changesTo('failed')`) and human review is not required, checks if `retry_count < max_retries`. If eligible, sets state to `waiting_for_retry` and sets `retry_time_out` to 5 minutes in the future.
5. **AI Clear Retry After Success:** Clears `retry_time_out` when state transitions to `complete` (`changesTo('complete')`).
6. **AI Protect Human Review:** Fires **only** when `Human Review Required` changes to `true` (`current.x_2215689_ai_inc_0_human_review_required.changesTo(true)`), setting state to `awaiting_approval` and clearing retry timeouts. Once approved and updated to `complete`, the flag does not re-trigger the rule because it did not transition to `true` on that save.

# Sprint 4.2: Correct ServiceNow Field Mapping — Work Notes vs. AI Resolution

**Workstream:** Advanced Agentic Incident Resolution Platform — Sprint 4 of 4: Stabilization & Data Correctness  
**Task Code:** S4.2 — BARQ G2: Correct ServiceNow Field Mapping: Work Notes vs AI Resolution  
**Date:** October 2026  
**Status:** Completed & Verified  

---

## 1. Executive Summary & Problem Context

Prior to this implementation, the incident resolution write-back logic conflated human-authored approval feedback and AI-generated remediation content. In several execution paths (such as when guardrails triggered, or when human solutions were provided during approval), raw human text was placed directly into the `AI Resolution` field. This caused **data blending**: anyone viewing the ServiceNow ticket would see human-authored text inside a field designated solely for AI output, obscuring the true provenance of the solution.

### Objective of S4.2:
1. **Strict Author Segregation**:
   - **Human-Authored Content**: Comments, decisions, and guidance from human reviewers belong exclusively in the ServiceNow **Work Notes** journal field (`work_notes`).
   - **AI-Authored Content**: The final synthesized resolution belongs exclusively in the **AI Resolution** field (`x_2215689_ai_inc_0_ai_resolution` / `close_notes`).
2. **AI Synthesis with Human Context**:
   - When a human provides resolution comments during an approval decision, the AI agent must synthesize the final procedure incorporating that feedback, explicitly prefacing its response:
     ```text
     Based on the human comments: <human_comments>, the solution of this incident is:
     1. <Procedural step> [Source: KBxxxx]
     2. <Procedural step> [Source: KByyyy]
     ```
3. **Collision & Overwrite Prevention**:
   - Neither field may silently overwrite, conflate, or absorb the other when both human comments and AI outputs exist on the same incident record.

---

## 2. Architecture & Field Mapping Logic

The platform enforces field separation through isolated tool dispatch paths in the Tool Registry, preventing accidental field mixing at the network payload level.

```
                              Human Approval Event
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │           interrupt_node          │
                     └─────────────────┬─────────────────┘
                                       │
                        route_after_human_review
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │           generate_node           │
                     │  Synthesizes solution incorporating│
                     │  human feedback + KB citations    │
                     └─────────────────┬─────────────────┘
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │             act_node              │
                     └─────────┬───────────────────┬─────┘
                               │                   │
                     Tool: write_ai_fields       Tool: write_work_note
                               │                   │
                               ▼                   ▼
                     ServiceNow PATCH payload   ServiceNow Journal
                     [AI Resolution Field]      [Work Notes Field]
                     "Based on human comments..." "[Human Review - Alice]: ..."
```

### Dedicated Field Mapping Table

| Source / Author | Content Type | Target ServiceNow Field | Method / Tool Dispatch |
|---|---|---|---|
| **Human Reviewer** | Reviewer Comments / Manual Solution | `work_notes` (Activity Stream) | `ToolRegistry.dispatch("write_work_note", ...)` |
| **AI Agent** | Evidence-backed Resolution & Citations | `x_2215689_ai_inc_0_ai_resolution` / `close_notes` | `ToolRegistry.dispatch("write_ai_fields", ...)` |
| **System / Guardrails** | Failure / Blockage Reason | `x_2215689_ai_inc_0_ai_failure_reason` | `ToolRegistry.dispatch("write_ai_fields", ...)` |
| **System / Pipeline** | Processing State / Confidence / Audit | `x_2215689_ai_inc_0_u_ai_processing_state` | `ToolRegistry.dispatch("write_ai_fields", ...)` |

---

## 3. Comprehensive Write Path Coverage

The separation logic is enforced across all four runtime write paths:

### Path A: Autonomous Resolution (No Human Intervention)
- **Condition**: Confidence $\ge$ 0.6, low/medium risk, output passes Critic and Guardrails.
- **Write Actions**:
  - `write_ai_fields`: Populates `AI Resolution` with the generated steps and citations. Sets `processing_state = complete`.
  - `write_work_note`: Not called (no human feedback exists).

### Path B: Human-in-the-Loop (HITL) Approved Resolution
- **Condition**: Ticket paused at approval gate (e.g. high risk or verification doubt); reviewer approves and enters guidance/solution.
- **Execution Flow**:
  1. `interrupt_node` receives `human_decision` and `human_solution`.
  2. `route_after_human_review` routes to `generate_node`.
  3. `generate_node` formats prompt with `human_solution` + KB evidence and enforces the introductory statement:
     `Based on the human comments: <human_solution>, the solution of this incident is:\n...`
  4. `act_node` executes isolated tool calls:
     - `write_ai_fields`: Writes the synthesized AI resolution to the AI Resolution field, sets `processing_state = complete`, and clears `failure_reason = None`.
     - `write_work_note`: Posts the human reviewer's comment formatted as:
       `[Human Review - <Reviewer Name>]: <human_solution>`
       directly into the ServiceNow Work Notes journal.
  5. `_sync_servicenow_completion` in `runtime_integration.py` guarantees that `outputs["resolution"]` takes absolute precedence over raw human text, populating both `AI Resolution` and `close_notes` with the AI-authored synthesis.

### Path C: HITL Rejected / Escalated
- **Condition**: Reviewer rejects the AI proposal or escalates.
- **Execution Flow**:
  1. `act_node` identifies `action_taken == "rejected_by_human"`.
  2. `write_ai_fields`: Sets `processing_state = failed`, `human_review = false` (clearing the approval flag so ServiceNow's *AI Prepare Retry* business rule does not trap the record in `awaiting_approval`), and records `failure_reason = "Rejected by <reviewer>: <comment>"`.
  3. `write_work_note`: Formats and writes reviewer's rejection note to `work_notes`:
     `[Human Review - <reviewer>]\nDecision: Rejected\nComment: <comment>`.

### Path D: Guardrail / Critic Blocked with Human Override
- **Condition**: AI draft blocked by security guardrails or exhausted critic, followed by human decision.
- **Execution Flow**:
  1. Human comments are preserved in `work_notes`.
  2. AI resolution field explicitly states the human contextualization rather than raw unverified code, preserving author clarity.

---

## 4. Collision and Overwrite Safeguards

1. **Independent HTTP Payloads**:
   - `write_ai_fields` uses `PATCH /api/now/table/incident/{sys_id}` with field-level attributes.
   - `write_work_note` uses the dedicated journal interface (`client.add_work_note(sys_id, note)`), targeting `work_notes`.
   - Neither payload contains fields belonging to the other, eliminating field collisions.
2. **Resolution Precedence Guard**:
   - In both `act_node` and `_sync_servicenow_completion` (`runtime_integration.py`), `outputs["resolution"]` is given strict priority over `checkpoint["human_solution"]`.
   - Raw human text is never written to `AI Resolution` without the synthesized prefix.
3. **Write Ordering for Crash Recovery**:
   - `write_ai_fields` runs first.
   - `write_work_note` runs second.
   - `write_execution_log` (idempotency receipt) runs last.
   - *Rationale*: If a worker crashes between steps, a retry safely re-applies the note without losing audit receipts.
4. **Verification & ACL Handling**:
   - Both client methods perform verification read-backs.
   - Read-back lag on ServiceNow journal fields (`sys_journal_field`) is logged as a non-fatal warning (`ServiceNowWriteNotAppliedError`), preventing worker crashes while ensuring payload delivery.

---

## 5. Verification Evidence & Test Results

### Automated Test Suites

1. **Targeted Work Notes & Segregation Tests (`tests/test_work_notes.py`)**:
   - **20 passed** in 3.11s.
   - Covers: note header formatting (`[Human Review - {name}]`), empty note suppression, client payload isolation, `act_node` dispatch segregation.
2. **Node Unit Tests (`tests/test_nodes.py`)**:
   - **43 passed** in 19.87s.
   - Validates all 11 individual graph nodes.
3. **Graph Lifecycle & HITL Integration Tests (`tests/test_graph.py`)**:
   - **6 passed** in 78.30s.
   - Validates resume paths, conditional edges, and end-to-end execution.

```bash
# Test Execution Command:
conda run -n barq-orch python -m pytest tests/test_work_notes.py tests/test_nodes.py tests/test_graph.py -q
# Result: 69 passed in 101.28s
```

---

## 6. Live ServiceNow Record Demonstration

To demonstrate compliance on an active ServiceNow incident record:

### Incident Demonstration Screenshot:
*(Insert screenshot of resolved ServiceNow Incident record below)*

![ServiceNow Work Notes vs AI Resolution Separation](images/sprint4_servicenow_record_evidence.png)

### Observed Record Verification:
1. **Activity Stream (Work Notes)**:
   - Contains: `[Human Review - Reviewer Name]: <Reviewer comments entered during approval>`
   - Does NOT contain the AI's generated procedure or citations.
2. **Resolution Tab / AI Resolution Field**:
   - Contains: `Based on the human comments: ..., the solution of this incident is:\n1. ... [Source: KBxxxx]`
   - Clearly authored by the AI model citing knowledge base references.
3. **State & Audit**:
   - Incident State: `Resolved` / `Closed`
   - AI Processing State: `Complete`
   - Human Review Required: Cleared / False

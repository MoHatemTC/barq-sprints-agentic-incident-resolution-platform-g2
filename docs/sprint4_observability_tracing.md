# Sprint 4 Observability & Structured Tracing Specification

## 1. Overview & Objectives

In Sprint 4, the platform observability layer was upgraded to address two critical operational issues:
1. **Trace Readability & Signal-to-Noise Ratio**: Previously, `@trace_node` dumped the entire 50-key `AgentState` dictionary into each child span's `input` and `output`. This made traces difficult to inspect in the Langfuse UI and introduced unnecessary payload bloat.
2. **Span Hierarchy Integrity**: The newly introduced `formulate_query` node invoked the LLM without passing `get_llm_callback()`, causing its LLM generation span to float at the root trace level rather than appearing nested under `node.formulate_query`.

---

## 2. Structured Node I/O Architecture

To maintain high observability without payload clutter, `src/observability/tracing.py` introduced two dedicated transformation helpers:
- `build_node_input(node_name: str, state: dict) -> dict`
- `build_node_output(node_name: str, result: dict) -> dict`

Each node now emits a human-readable, domain-specific summary captured directly into Langfuse:

| Node Name | Curated Input Summary | Curated Output Summary |
| :--- | :--- | :--- |
| `formulate_query` | `incident_number`, `short_description`, `description`, `human_solution` | `search_query` |
| `classify` | `incident_number`, `short_description`, `description`, `human_solution_present` | `classification` |
| `retrieve` | `incident_number`, `search_query`, `category`, `service` | `evidence_count`, `top_ids_scores`, `cache_hit`, `retrieval_failed` |
| `diagnose` | `incident_number`, `evidence_count`, `evidence_ids`, `description` | `root_cause`, `confidence`, `supporting_evidence` |
| `generate` | `incident_number`, `diagnosis`, `evidence_count`, `is_revision`, `revision_count` | `resolution_preview`, `revision_count` |
| `safety_check` | `incident_number`, `resolution_preview` | `action_taken`, `safety_passed` |
| `confidence_check` | `incident_number`, `confidence`, `critic_exhausted` | `confidence`, `critic_exhausted` |
| `act` | `incident_number`, `resolution_preview`, `human_decision`, `risk` | `action_taken`, `servicenow_write` |
| `knowledge_capture` | `incident_number`, `human_solution`, `classification` | `status`, `article_number` |

---

## 3. Span Hierarchy & LLM Callbacks

Under the Langfuse v4 architecture, LLM generations must be explicitly linked to their parent node span using `get_llm_callback()`:
- `_current_trace_id` propagates the root trace identifier across Celery worker executions.
- `_current_span_id` dynamically tracks the active node span.
- `get_llm_callback()` binds the LangChain `CallbackHandler` to both the trace ID and the current `parent_observation_id`.

In `src/agent/nodes/formulate_query.py`, the LLM invocation was updated to:
```python
response = llm.invoke(prompt, config={"callbacks": get_llm_callback()})
```
This guarantees the visual tree hierarchy in Langfuse:
```
Root Execution Trace (trace_execution)
 └── node.load
 └── node.validate
 └── node.classify
      └── LLM Generation (Category Selection)
 └── node.determine_risk
      └── LLM Generation (Risk Analysis)
 └── node.formulate_query
      └── LLM Generation (Optimized Search Query)
 └── node.retrieve (Qdrant Hybrid Search)
 └── ...
```

---

## 4. Edge Cases & Resilience Safeguards

### 4.1 ServiceNow Write-Back Auditing (`act` Node)
The `act` node executes external writes to ServiceNow and returns:
```python
{"action_taken": "resolved_automatically", "servicenow_write": "written"}
```
`build_node_output("act", ...)` explicitly captures `servicenow_write` rather than relying on internal state dictionaries, ensuring audit trails verify whether updates were `written`, `already_done`, or `skipped_no_sys_id`.

### 4.2 Query Formulation Continuation Fallbacks
When an incident is routed through Human-in-the-Loop (HITL) review and approved with reviewer guidance (`human_solution`), that guidance must reach retrieval even if the LLM query generation fails or returns empty. 

Both fallback paths in `formulate_query_node` append the reviewer guidance:
```python
if human_solution:
    search_query += f"\nHuman-provided resolution:\n{human_solution}"
```
This preserves retrieval continuity and ensures reviewer guidance grounds subsequent search queries.

---

## 5. Verification & Test Coverage

Regression and contract tests in `tests/test_nodes.py`:
- `test_formulate_query_fallback_includes_human_solution`: Validates that LLM runtime failures preserve `human_solution` in the fallback search query.
- `test_act_node_tracing_output`: Validates that `build_node_output("act", ...)` preserves `action_taken` and `servicenow_write`.
- Full regression suite across graph execution and observability passes with 0 failures.

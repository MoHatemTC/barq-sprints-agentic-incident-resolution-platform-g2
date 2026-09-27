# Sprint 3.1 — Agent Topology & Latency Documentation

## Overview

Sprint 3.1 decomposes the monolithic `diagnose → generate → verify_evidence` stub
sequence into three specialised cooperating agents operating inside the existing
single LangGraph execution, single Langfuse trace, and single Postgres execution record.

---

## Three-Agent Architecture

```
Retrieved Evidence (from retrieve_node)
             │
             ▼
  ┌─────────────────────────────┐
  │      Diagnostic Agent       │  src/agent/nodes/diagnose.py
  │  - Root cause analysis      │  Prompt: DIAGNOSTIC_SYSTEM_PROMPT
  │  - Evidence-grounded        │  Output: outputs["diagnosis"] (str)
  │  - No remediation           │          outputs["diagnosis_structured"] (dict)
  └─────────────┬───────────────┘
                │ diagnosis + evidence
                ▼
  ┌─────────────────────────────┐
  │      Resolution Agent       │  src/agent/nodes/generate.py
  │  - Numbered procedure       │  Prompt: RESOLUTION_SYSTEM_PROMPT (initial)
  │  - Citations [Source: KB_X] │          RESOLUTION_REVISION_SYSTEM_PROMPT (revision)
  │  - Accepts critic feedback  │  Output: outputs["resolution"] (str)
  └─────────────┬───────────────┘
                │ draft resolution
                ▼
  ┌─────────────────────────────┐
  │    Critic / Verifier Agent  │  src/agent/nodes/verify_evidence.py
  │  - Citation existence check │  Prompt: CRITIC_SYSTEM_PROMPT
  │  - Plausibility check (LLM) │  Output: critic_verdict (dict)
  │  - Structured JSON verdict  │          outputs["verification_passed"] (bool)
  └─────────────┬───────────────┘
                │
       ┌────────┼──────────┐
    PASS      FAIL       FAIL
       │    (retries)  (exhausted)
       ▼       ▼           ▼
  safety_  generate     act
  check    (revision)   (critic_exhausted=True)
```

---

## Retry Loop Sequence

```
diagnose
  │
  └──► generate [initial, revision_count=0]
           │
           └──► verify_evidence
                    │
                    └──► route_after_critic
                               │
                    ┌──────────┼──────────────┐
                 PASS       FAIL            FAIL
                            (retries remain) (critic_exhausted=True)
                    │          │                │
               safety_check  generate         act
                    │        (revision)   (degraded path)
               confidence_check
                    │
                   act
```

### Exhaustion Calculation

`verify_evidence_node` sets `state["critic_exhausted"]` by comparing
`state["revision_count"]` with `AGENT.critic_max_retries` (from `CRITIC_MAX_RETRIES`,
default `2`). `route_after_critic` then chooses the deterministic next step:

```
Initial call:  revision_count = 0  →  not exhausted
After rev 1:   revision_count = 1  →  not exhausted (if max_retries=2)
After rev 2:   revision_count = 2  →  EXHAUSTED (critic_exhausted=True)
```

With the default of `2`, there are up to **3 LLM generation calls**:
initial + revision 1 + revision 2.

---

## Langfuse Span Hierarchy

All three agents use the `@trace_node` decorator, following the exact same
pattern as all pre-existing nodes (`classify`, `determine_risk`, etc.).

```
[Root Trace] trace_execution — keyed to execution_id
  ├── [Span] load
  ├── [Span] validate
  ├── [Span] classify
  │     └── [Generation] LLM call (via get_llm_callback())
  ├── [Span] determine_risk
  │     └── [Generation] LLM call
  ├── [Span] retrieve
  ├── [Span] diagnose              ← Diagnostic Agent
  │     └── [Generation] LLM call
  ├── [Span] generate              ← Resolution Agent (initial)
  │     └── [Generation] LLM call
  ├── [Span] verify_evidence       ← Critic Agent
  │     └── [Generation] LLM call (only when structural check passes)
  │  (On FAIL with retries remaining:)
  ├── [Span] generate              ← Resolution Agent (revision N)
  │     └── [Generation] LLM call
  ├── [Span] verify_evidence
  │     └── [Generation] LLM call
  ├── [Span] safety_check          ← S3.3 stub
  ├── [Span] confidence_check
  └── [Span] act / interrupt
```

**Each node** records: input state keys, output state delta, latency, and any
error. **LLM calls** are recorded as nested generation spans via the Langfuse
LangChain callback handler (`get_llm_callback()`).

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `CRITIC_MAX_RETRIES` | `2` | Maximum revision cycles before routing directly to `act` |
| `LITELLM_BASE_URL` | (required) | LiteLLM proxy base URL |
| `LITELLM_API_KEY` | (required) | API key for LiteLLM proxy |
| `LLM_MODEL` | `gemini-3.6-flash` | Model used by all three agents |

All agent config is in `src/config.py` under the `AgentConfig` dataclass,
instantiated as the `AGENT` singleton. This follows the same pattern as
`RETRIEVAL`, `CHUNKING`, `WORKER`, etc.

---

## Latency Profile (measured in test harness, all LLM mocked)

| Node | Observed latency (mock) | Expected latency (real LLM) |
|---|---|---|
| `diagnose` | < 5 ms | 500–2000 ms (1 LLM call) |
| `generate` (initial) | < 5 ms | 500–2000 ms (1 LLM call) |
| `verify_evidence` (structural fail) | < 1 ms | < 1 ms (no LLM) |
| `verify_evidence` (LLM plausibility) | < 5 ms | 500–2000 ms (1 LLM call) |
| Full loop (pass on attempt 1) | ~15 ms | 1.5–6 s |
| Full loop (1 revision, then pass) | ~20 ms | 2–8 s |
| Full loop (exhausted, 2 revisions) | ~25 ms | 2.5–10 s |

> **Note:** Real-world p95 latency is dominated by LLM API round-trip time.
> The additional routing and state-checking overhead introduced by S3.1 is
> negligible (< 5 ms per extra node traversal).

---

## Cross-Team Boundary Summary

| Sprint | Boundary |
|---|---|
| **S3.2** — Tool Registry | S3.1 does not implement tool permissions or allowlists |
| **S3.3** — Guardrails | `safety_check_node` is a stub; S3.1 does not touch it |
| **S3.4** — HITL | `act_node` and `interrupt_node` are not modified; `critic_exhausted=True` in state exposes the degraded path for S3.4 to gate |
| **S3.5** — Knowledge Capture | S3.1 does not write to ServiceNow KB or Qdrant |

---

## Files Changed by S3.1

| File | Change |
|---|---|
| `docs/sprint3_multi_agent_design.md` | [NEW] Audit & design baseline document |
| `src/agent/prompts.py` | [NEW] Four system prompts for the three agents |
| `src/agent/nodes/diagnose.py` | [MODIFIED] Full Diagnostic Agent implementation |
| `src/agent/nodes/generate.py` | [MODIFIED] Full Resolution Agent with revision support |
| `src/agent/nodes/verify_evidence.py` | [MODIFIED] Full Critic/Verifier Agent |
| `src/agent/graph.py` | [MODIFIED] Critic routing loop through `route_after_critic` |
| `src/agent/state.py` | [MODIFIED] Three new fields: `critic_verdict`, `revision_count`, `critic_exhausted` |
| `src/config.py` | [MODIFIED] `AgentConfig` dataclass with `CRITIC_MAX_RETRIES`, `AGENT` singleton |
| `tests/test_nodes.py` | [MODIFIED] +36 tests for all three agents and their helpers |
| `tests/test_revision_loop.py` | [NEW] End-to-end integration tests for the retry loop |

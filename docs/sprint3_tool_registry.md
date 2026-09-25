# Sprint 3 Tool Registry — Permission Classification

## Least-Privilege Classification Principle

Permission class is determined not by *whether* an action mutates ServiceNow
state, but by two factors that together define its blast radius:

1. **Scope** — is the effect confined to a single incident, or does it persist
   as shared state that outlives the ticket that created it?
2. **Consumer** — is the output reviewed by a human before it influences
   anything downstream, or is it consumed directly by other automated
   workflows with no human in the loop?

Actions that are single-incident-scoped and either unread or human-reviewed
before use carry low blast radius even when they write. Actions that produce
unscoped, persistent, machine-trusted state carry high blast radius
regardless of how "small" the write itself looks — because a bad write isn't
caught by any human before it propagates.

## Per-Tool Classification

| Tool | Permission Class | Reasoning |
|---|---|---|
| `read_incident` | `read` | Zero mutation — queries incident fields without modifying any state. |
| `write_execution_log` | `low_risk_write` | Purely internal bookkeeping. Appends diagnostic telemetry and execution logs that neither agents nor customers rely on for ticket resolution. Single-incident-scoped, effectively unread by downstream consumers. |
| `write_ai_fields` | `low_risk_write` | Dedicated AI scratchpad. Updates model suggestions and analysis fields, strictly restricted by the S1.2 ACL boundary from touching core operational fields (`state`, `priority`, `assigned_to`). Single-incident-scoped, bounded by existing ACL. |
| `write_work_note` | `low_risk_write` | Visible internal record — adds an entry to the incident's activity journal for IT support agents to review and triage from. Single-incident-scoped, and critically, a human agent independently reviews the ticket before acting — the human stays in the loop as a check on this write. |
| S3.5 KB write-back | `high_risk` | Shared organizational state — creates or updates records in `kb_knowledge`, read, trusted, and acted on by other engineers *and automated workflows* across the platform. Breaks the pattern on both axes at once: the write is unscoped (persists beyond the originating incident) and machine-consumed (removes the human-in-the-loop safety net that implicitly protects `write_work_note`). A bad KB entry doesn't just mislead one agent on one ticket — it can be pulled into future incident resolutions by other automation with no human catching it first. Gated under A-12 human approval accordingly. |

## Summary

> Scoped, single-incident, human-reviewed actions → `low_risk_write`.
> Unscoped, platform-wide, machine-consumed state → `high_risk`, gated on A-12 approval.

---

## Server-Side Enforcement Mechanics

**`ToolRegistry.dispatch()` is the single structural choke point.** Every tool
call — a read, a low-risk write, or the S3.5 KB write-back — passes through
`dispatch()` before any handler, and therefore before any ServiceNow network
call, can run. There is no second door.

**Enforcement flow.** `dispatch(name, execution_id, **kwargs)` runs a fixed
order of steps:

1. **Look up the tool by name.** An unregistered name returns a typed
   `ToolRefusal(reason="unregistered_tool")` immediately — before any further
   work and before any handler is considered.
2. **Log the dispatch attempt.** The tool name, `execution_id`, and the tool's
   permission class are logged *before any decision*, so every attempt —
   refused or approved — is auditable.
3. **Call `permissions.is_approved(execution_id, permission_class)`**.
   `READ` and `LOW_RISK_WRITE` always pass. `HIGH_RISK` requires a matching,
   unconsumed approval record.
4. **Only if approved, invoke the handler.** A failed gate returns a typed
   `ToolRefusal(reason="approval_required")`; the handler is never invoked.

**Approval binding and replay safety.** Approvals are bound to a specific
`execution_id` — one discrete agent run and its action proposal — never to the
tool name alone and never to a freshly minted ID per call attempt. Binding too
broadly (by tool, or by incident number) creates replay risk: a single grant
could authorize repeated, unrelated destructive writes because every matching
dispatch would reuse the same approval. Binding too narrowly (a fresh ID per
retry) breaks legitimate retries and resumed processes, because the new ID
would not match the approval that was actually inspected and granted.

**Atomicity.** The check-and-consume for `HIGH_RISK` is one atomic
`UPDATE ... WHERE consumed = false ... RETURNING` statement, not a separate
`SELECT` then `UPDATE`. A SELECT-then-UPDATE would let two concurrent dispatch
attempts both observe `consumed = false` and both pass the gate before either
marks the approval consumed. The single guarded statement closes that race; a
12-thread concurrent test asserts exactly one racing dispatch succeeds.

**Fail-closed by construction.** Any ambiguity or missing data — a missing
`execution_id`, an unknown permission class, a database error during the
consume — resolves to refusal, never execution. A broken approval path denies
by default, and a false denial is strictly safer than a wrongly-executed
high-risk write.

Combined with the import-boundary test forbidding any code path from invoking
IncidentGateway methods outside the registry, this makes the registry dispatch
the sole enforcement point — attacking it directly is a meaningful test of the
whole system's integrity, not just the happy path.

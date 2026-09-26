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

## Registry Extension Contract

Adding a tool is not just a handler and a `register()` line. The registry is
the system's only permission boundary, so a tool added without every step below
either silently escapes enforcement or fails at runtime in production. Follow
the steps in order.

### 1. Classify the permission, then justify the classification

Pick the narrowest class the tool's actual effect allows, and record why in
`sprint3_tool_registry.md` alongside the tool. The question is not *what the
tool is for* but *what it can change*:

| Class | Use for | Approval |
|---|---|---|
| `READ` | Cannot change remote state. Reads, existence probes, no-op paths. | Never |
| `LOW_RISK_WRITE` | Writes bounded, reversible fields; no destructive or outward-facing effect. | Not required |
| `HIGH_RISK_WRITE` | Destructive, unbounded, or outward-facing: deletes, bulk changes, anything a human or customer sees. | Required and consumed exactly once |

A new tool defaults to `HIGH_RISK_WRITE` until proven otherwise. Downgrading
needs a reason, not just the absence of objection — a read that can fail
partway and leave state changed is a write.

### 2. Implement the handler on IncidentGateway

Handlers live on `IncidentGateway` in `src/servicenow/client.py`, receive
`execution_id` as the first argument, and take only keyword arguments after
it. `dispatch()` always supplies `execution_id`, so a handler that does not
accept it raises `TypeError` at the first call. Verified by
`test_every_registered_tool_dispatches_without_typeerror`.

Never construct a `ServiceNowClient` outside `IncidentGateway`. It is the
innermost client, holds auth, and is deliberately not injectable from nodes.

### 3. Register it

In `DEFAULT_TOOL_REGISTRY` in `src/agent/tools/registry.py`:

```python
registry.register("write_ai_fields", PermissionClass.LOW_RISK_WRITE, gateway.write_ai_fields)
```

Names are the wire contract between the LLM's tool call and the handler; keep
them stable once shipped.

### 4. Dispatch it — the step that is easy to skip

Call `registry.dispatch(name, execution_id, **kwargs)`, never the handler
directly. Skipping this was a real defect: `act_node` and `load_node` were both
calling `ServiceNowClient` directly, so the registry was correct, fully tested,
and completely bypassed by the code that actually wrote to ServiceNow. The
boundary tests could not see it because they dispatched in isolation rather
than driving the real nodes. **Routing the call is the point of the tool.**

### 5. Handle ToolRefusal, never just return it

`dispatch()` signals refusal by *returning* a `ToolRefusal` — it does not
raise. A caller that ignores the return value falls through and reports a
success it did not have. Check the result:

- `act_node` raises `ToolRefused` (`retryable = False`): a refused write is a
  terminal failure, not a retry. Retrying cannot register a missing tool or
  conjure an unconsumed approval.
- `load_node` warns and continues: a missing incident is genuinely tolerable
  there, and refusing to run would be a worse failure than reading nothing.

### 6. Prove it

- Refusal coverage: unregistered *and* unapproved, driving the real call site,
  asserting the write did not reach the client.
  See `tests/test_act_node_registry_integration.py`.
- Import-boundary coverage: `tests/test_registry_enforcement.py` fails if any
  new module outside `ALLOWED_DIRECT_CLIENT` constructs a client.

### Current exceptions to the boundary

`ALLOWED_DIRECT_CLIENT` in `tests/test_registry_enforcement.py` enumerates
reviewed non-agent paths. New entries need a justification in that list:

- `src/api/routers/dashboard.py` — human-triggered incident creation. No
  execution id exists to dispatch with, and it is not an agent action.
- `src/retrieval/sources/servicenow_source.py` — read-only KB synchronization.
- `src/api/dependencies.py` — a dead DI placeholder that shares the class name
  and raises `NotImplementedError`. Name collision only; renaming it is the
  real fix and is out of scope here.

Agent code has no exceptions. If a node, the worker, or an orchestrator path
needs a ServiceNow call, it goes through `dispatch()` like any other tool.

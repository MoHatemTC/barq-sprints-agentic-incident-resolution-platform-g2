# Sprint 4 — S4.4 UI Dashboard and ServiceNow Two-Way Sync

**Author:** Hady Elfadaly, **Branch:** `feat/s4.4-dashboard-servicenow-sync`

## Summary
The dashboard and ServiceNow no longer drift apart. The incident list is read **live from ServiceNow** (no copy in Postgres to go out of date), and each incident shows its latest AI run from Postgres. Everything the platform does goes back to ServiceNow: incidents created or deleted on the dashboard, and every AI status change, work note and execution log written by the agent. The dashboard also shows the **full run history** of each incident, with every step's log.

Every view shows how fresh its data is. During a ServiceNow or API outage the last good data stays on screen with its age and the reason, and nothing is silently dropped or duplicated.

| Deliverable | Where |
|---|---|
| Interface | [`ui/dashboard.html`](../ui/dashboard.html), [`ui/dashboard.js`](../ui/dashboard.js) (+ [`ui/common.js`](../ui/common.js), [`ui/styles.css`](../ui/styles.css)) |
| API router | [`src/api/routers/dashboard.py`](../src/api/routers/dashboard.py) |
| ServiceNow client | [`src/servicenow/client.py`](../src/servicenow/client.py) |
| Decide from ServiceNow | [`src/api/routers/approvals.py`](../src/api/routers/approvals.py) (`/approvals/by-incident/{sys_id}/decide`) |
| Latency benchmark | [`scripts/bench_dashboard.py`](../scripts/bench_dashboard.py) → [`eval/results/dashboard_latency.md`](../eval/results/dashboard_latency.md) |
| Tests | 95 passing across 6 files (see [§8](#8-tests)) |
| Live demonstration | Script: [§9](#9-live-verification-demo-script). Screenshots from the live run: [§10](#10-evidence-live-run-2026-10-03) |

**Decisions confirmed with the mentor (Sarah Nader):**
- Dashboard reads, creates and deletes reuse `ServiceNowClient` directly. `ToolRegistry` stays reserved for agent execution governance, which is where its permission checks belong.
- Data counts as stale after **30 s**, on top of the **10 s** cache.
- Run history is shown in a drawer.

---

## 1. Sync Architecture

```mermaid
flowchart LR
    subgraph SN[ServiceNow]
        INC[(incident)]
        BR[AI Eligibility Check<br/>Business Rule]
        UIA[Approve AI / Reject AI<br/>Human Governance tab]
        LOG[(AI execution log)]
    end
    subgraph P[Platform]
        WH[Webhook<br/>Bearer · idempotent]
        SW[Delivery sweep<br/>every 60 s]
        PG[(Postgres<br/>runs · logs · approvals)]
        AG[Celery worker<br/>LangGraph agent]
        REG[ToolRegistry]
        DASH[Dashboard API<br/>/api/v1/dashboard]
    end
    UI[Dashboard UI]

    INC -- insert / update --> BR -- event --> WH --> PG --> AG
    SW -- catch-up read --> INC
    SW --> PG
    AG -- AI fields, work notes, log --> REG --> INC & LOG
    UIA -- decision --> WH2[Approvals API] --> AG
    UI -- poll 3 s --> DASH
    DASH -- list, 10 s cache --> INC
    DASH -- AI status, run history --> PG
    UI -- create / delete --> DASH -- ServiceNowClient --> INC
```

**ServiceNow → platform and dashboard**

| What changes in ServiceNow | How it reaches us |
|---|---|
| Incident created or updated (any field) | The dashboard list reads ServiceNow directly ([`list_incidents`](../src/api/routers/dashboard.py#L435)), so the change appears on the next refresh |
| Incident becomes eligible for AI | The Business Rule (after insert/update, [`update_set.xml`](../servicenow/ai_incident_orchestrator/update_set.xml)) posts it to the webhook, which returns 202 and is idempotent on `event_id` ([`webhook.py`](../src/api/routers/webhook.py)) |
| The webhook call is lost (API down, tunnel down) | The delivery sweep ([`delivery_sweep.py`](../src/workers/delivery_sweep.py)) re-reads eligible Pending incidents every 60 s, after a 2-minute grace period |
| A reviewer decides in ServiceNow | Approve AI / Reject AI call [`/approvals/by-incident/{sys_id}/decide`](../src/api/routers/approvals.py#L299) with the webhook Bearer token. It runs the same checks as the dashboard Approvals page (solution required for high risk, first decision wins) |

**Platform → ServiceNow**

| What the platform does | How ServiceNow is updated |
|---|---|
| Run starts | `processing_state = in_progress` ([`sync_servicenow_in_progress`](../src/workers/runtime_integration.py#L415)) |
| Run pauses for a human | `processing_state = awaiting_approval`, plus a work note with the approval brief and how to decide ([`_sync_servicenow_pause_note`](../src/workers/runtime_integration.py#L204)) |
| Run finishes | `processing_state = complete`, classification, confidence, model and timings; the resolution fields only once the incident is actually resolved (approved or auto-resolved) ([`_sync_servicenow_completion`](../src/workers/runtime_integration.py#L330)) |
| Run fails | `processing_state = failed`, plus a work note with the error ([`_sync_servicenow_failure`](../src/workers/runtime_integration.py#L376)) |
| Agent's act step | A row in the AI execution log table (`write_execution_log`, [`act.py`](../src/agent/nodes/act.py)), which also serves as the write receipt |
| Incident created / deleted on the dashboard | `ServiceNowClient.create_incident` / `delete_incident`. Creating fires the Business Rule exactly as the ServiceNow form does |

**Reuse of the existing integration (no separate path):**
- **Agent writes** all go through `ToolRegistry`, with its permission classes ([`registry.py:138-148`](../src/agent/tools/registry.py#L138-L148)): `read_incident` / `list_incidents` are READ; `write_ai_fields`, `write_work_note` and `write_execution_log` are LOW_RISK_WRITE; `kb_write_back` is HIGH_RISK.
- **Dashboard CRUD** uses one shared `ServiceNowClient` ([`_servicenow`](../src/api/routers/dashboard.py#L219)), so its OAuth token is reused instead of fetched per poll. This sprint's client changes are `create_incident(correlation_id=…)` and `find_incident_by_correlation()` ([`client.py:76-104`](../src/servicenow/client.py#L76-L104)).
- **Writes are verified.** `update_incident` compares what was sent with what ServiceNow returned, and `add_work_note` reads the journal back, so an HTTP 200 that silently dropped a field is caught ([`client.py:173-231`](../src/servicenow/client.py#L173-L231)).

---

## 2. Trigger Strategy

Each direction uses the cheapest trigger that is still reliable:

| Trigger | Type | Interval / when | Where |
|---|---|---|---|
| Business Rule → webhook | Event-driven | On incident insert/update, async | ServiceNow update set |
| Delivery sweep | Polling (safety net) | Every 60 s, incidents untouched for over 2 min, last 24 h | [`delivery_sweep.py`](../src/workers/delivery_sweep.py) |
| Agent status write-back | Event-driven | On run start, pause, finish, failure | [`runtime_integration.py`](../src/workers/runtime_integration.py) |
| Dashboard incident list | Polling | UI every **3 s** while the tab is visible ([`dashboard.js:380`](../ui/dashboard.js#L380)); server reads ServiceNow at most once per **10 s** per page | [`dashboard.py:141`](../src/api/routers/dashboard.py#L141) |
| Create / delete on dashboard | On demand | The cache is cleared, so the change shows on the next poll, not after the TTL | [`dashboard.py:512`](../src/api/routers/dashboard.py#L512) |
| Run history / step log | On demand | When the drawer opens / when a step is expanded | [`dashboard.js:534`](../ui/dashboard.js#L534) |
| Refresh button, Live switch | On demand | Manual refresh; Live off stops polling | `ui/dashboard.js` |

**Why polling for the list rather than ServiceNow push:** a ServiceNow change to *any* field (state, priority, a human edit) should show up, not only the AI-eligible ones that fire the Business Rule. Reading the list directly means there is nothing to reconcile. The server cache is shared by every open tab, so ten tabs polling every 3 s still cost **one** ServiceNow call per page per 10 s. The list endpoint is a plain `def`, so a slow ServiceNow call runs in a worker thread and never blocks the webhook's event loop.

---

## 3. Freshness and Staleness Boundaries

Every incident page carries `synced_at`, the moment it was read from ServiceNow (not the moment it was served). The API returns:

| Field | Meaning |
|---|---|
| `synced_at` | When this copy was read from ServiceNow |
| `age_seconds` | How old the copy is now |
| `stale_after_seconds` | The staleness threshold (30) |
| `delayed` | The last refresh failed, so an older copy is being served |
| `stale` | `age_seconds > stale_after_seconds` |
| `sync_error` | One short reason, e.g. `ServiceNow could not be reached (timed out)` |

| State | Age of data | Dashboard shows |
|---|---|---|
| **Live** | ≤ 10 s (cache TTL) | Green "Live" badge, toolbar "Synced HH:MM:SS" |
| **Delayed** | ≤ 30 s, last refresh failed | Yellow "ServiceNow delayed" badge, data unchanged |
| **Stale** | > 30 s | Yellow "ServiceNow stale" badge + banner: *"Showing ServiceNow data from 16:41:20 (38s old). Last refresh failed: … Retrying automatically."* |
| **ServiceNow unreachable** | nothing cached yet for this page | Red badge, "Could not reach ServiceNow" with the reason |
| **API unreachable** | — | Red "API unreachable" badge + banner with the last sync time |

Thresholds are configurable: `DASHBOARD_INCIDENT_CACHE_SECONDS` (default 10) and `DASHBOARD_STALE_AFTER_SECONDS` (default 30).

**Worst-case lag in each direction (normal operation):**
- ServiceNow change → dashboard list: **≤ 13 s** (10 s cache + 3 s poll), plus one ServiceNow read (~1 s, see §5).
- Eligible incident → AI run started: seconds via the Business Rule; **≤ about 3 min** if the webhook was lost (2 min grace + 60 s sweep).
- Agent state change → ServiceNow: immediately at each run transition (start, pause, finish, failure).
- AI run → dashboard: ≤ 3 s (Postgres is read on every poll, not cached).

---

## 4. Failure Handling and Recovery

| Failure | Behaviour | Seen in the UI | Recovery | Data loss / duplicates |
|---|---|---|---|---|
| ServiceNow down while the list is cached | The last good page is served, marked `delayed` and then `stale`. Only one request at a time retries; the others get the cached copy immediately ([`_PageCache`](../src/api/routers/dashboard.py#L169)) | Badge, then banner with age + reason | Automatic on the next successful read | None: the last page is never dropped |
| ServiceNow down, nothing cached (first load, new search) | 502 with a short reason ([`_sync_error_text`](../src/api/routers/dashboard.py#L147)); the full error goes to the API log | "Could not reach ServiceNow" | Next poll | None |
| API down | — | "API unreachable" + last sync time | Next poll | None |
| Create times out after ServiceNow created the record | The form sends one `request_id` per opened form, stored as `correlation_id = barq-dashboard-<id>`. A retry finds the existing incident and returns it (200, `already_created`) ([`create_incident_via_dashboard`](../src/api/routers/dashboard.py#L512)) | Toast: *"Could not create incident: … Try again; it will not create a duplicate."* | User clicks Create again | **No duplicate** (verified live: the same request twice gives one incident) |
| Webhook delivered twice | Idempotency on `event_id`; the second returns 202 "Duplicate" | — | — | No duplicate run |
| Webhook never delivered | Delivery sweep catches it | Incident shows "Not sent to AI" until the sweep picks it up | Within about 3 min | None |
| Agent write-back to ServiceNow fails | Status writes are best-effort: `_dispatch_best_effort` logs them and never crashes the run. A failing write in the act step fails the attempt, which is recorded in `failures` and retried by the worker retry policy | Red failure box in the run history; `failed` status | Retry policy, then DLQ (see limitations) | Run state kept in Postgres |
| Two reviewers decide at once (ServiceNow + dashboard) | `claim_decision`: the first decision wins, the second gets 409 ([`approvals.py:77`](../src/api/routers/approvals.py#L77)) | Error on the second | — | One decision, one resume |

Error messages are short and readable. URLs, query strings and internal field names never reach the UI (tested). `ui/common.js` now also reads the API's `{"error": {"message"}}` shape, so error toasts on every page show the reason instead of "HTTP 502".

**Known limitations (open items):**
- A run that ends in the DLQ leaves no work note in ServiceNow yet.
- If the webhook persists the event but the enqueue fails, the event can wait until a retry (idempotency blocks a resend). The sweep does not cover it because the event already exists.
- The Approve AI / Reject AI UI Actions are configured on the instance and still need to be exported into `update_set.xml`.

---

## 5. Historical Log Retrieval and Performance

**Endpoints**

| Endpoint | Returns |
|---|---|
| `GET /api/v1/dashboard/incidents/{number}/runs?limit=10&before=<cursor>` | Every run of the incident, newest first: status, times, duration, node reached, model, retries, failures, human decisions, and the **list** of log steps (name + time, no payload). `next_before` is the cursor for older runs |
| `GET /api/v1/dashboard/runs/{execution_id}/log/{entry_id}` | One step's full payload (JSON), only when it is opened |

**Design choices that keep it fast as logs grow** ([`_run_page`](../src/api/routers/dashboard.py#L611))
- **Keyset paging** on `(started_at, id)`, not OFFSET, so the oldest page costs the same as the newest. Two runs that start at the same second are neither skipped nor repeated (tested).
- **A fixed 5 indexed queries per page** (runs, steps, failures, retries, approvals), whatever the history size: no N+1.
- **Payloads on demand.** A step payload averages ~5 KB and reaches ~42 KB in our data, so the list never carries them.
- Page size 10 by default, 50 max (validated).

**Measured latency** (p50 / p95 ms, 100 requests per cell, Postgres 16 in Docker on the dev machine; full report in [`eval/results/dashboard_latency.md`](../eval/results/dashboard_latency.md))

| Request | 1,000 runs | 10,000 runs | 100,000 runs |
|---|---|---|---|
| Run history, typical incident (2 runs) | 8.3 / 13.4 | 7.9 / 9.4 | 7.0 / 8.4 |
| Run history, heaviest incident (1,000 runs), newest page | 10.2 / 14.5 | 10.1 / 11.8 | 8.9 / 11.0 |
| Run history, heaviest incident, **oldest** page | 12.6 / 17.9 | 11.0 / 13.7 | 9.8 / 11.6 |
| One log entry payload (~5 KB) | 4.5 / 6.0 | 4.2 / 4.9 | 4.0 / 4.7 |
| Incident list + AI status, 20 rows (our side) | 15.8 / 20.1 | 17.5 / 22.8 | 11.3 / 14.1 |
| Incident list + AI status, 100 rows | 33.0 / 41.3 | 42.7 / 55.0 | 30.9 / 39.2 |
| Incident list + AI status, 500 rows (max) | 74.6 / 98.4 | 146.1 / 197.6 | 119.0 / 155.4 |
| **Live ServiceNow** incident page, 20 rows | p50 945, p95 1,189, max 1,320 (10 samples) | | |

At 100,000 runs, Postgres reads the 1,000-run incident through the existing `ix_executions_incident_reference` index in **0.5 ms**, so no new index or migration is needed.

**Documented latency bounds** (server side, p95, with headroom over the measurements):

| Request | Bound |
|---|---|
| Run history page | **≤ 50 ms**, independent of history size up to 100k runs |
| One log entry | **≤ 25 ms** |
| Incident list, ≤ 100 rows, cache hit | **≤ 75 ms** |
| Incident list, 500 rows (max page) | **≤ 250 ms** |
| Incident list, cache miss (includes ServiceNow) | **≤ 2 s**. Above `SERVICENOW_TIMEOUT` (30 s) the last good copy is served and marked stale |

Re-run the benchmark: `.venv/bin/python scripts/bench_dashboard.py [--live-servicenow 10]`. It builds a throwaway `orchestrator_bench` database migrated to head, seeds it and drops it at the end. The real database is never touched.

---

## 6. Dashboard UI

- **Incident list:** every ServiceNow incident (number, description, category, state) with its latest AI run: status chip, pipeline stage track and duration. Search runs in ServiceNow (number or description words); "Load more" pages through older incidents.
- **Expanded row:** incident facts, the latest run's classification, risk, confidence, outcome, diagnosis and resolution, plus the buttons **Run history** and **Open in ServiceNow ↗**.
- **Run history drawer:** one card per run, newest first (the current one has a blue border). Each card shows status, start time, duration and execution ID; node reached, retries and model; the **human decision** (who, when, solution); failures in red; and every step with a readable label ("Paused for human review", "Resumed after a worker crash", "Final result") and **View log** for its JSON. "Load older runs" pages through older runs.
- **New incident:** creates the incident in ServiceNow only. The Business Rule decides whether it is processed, exactly as for the ServiceNow form. The dashboard then watches for the webhook event and reports whether it arrived.

---

## 7. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_INCIDENT_CACHE_SECONDS` | 10 | How long one ServiceNow incident page is reused |
| `DASHBOARD_STALE_AFTER_SECONDS` | 30 | Age after which the dashboard warns |
| `SERVICENOW_TIMEOUT` | 30 | Per-request timeout to ServiceNow |
| `SERVICENOW_DEFAULT_CALLER_SYS_ID` | — | Caller set on incidents created from the dashboard |

---

## 8. Tests

| File | Tests | Covers |
|---|---|---|
| [`test_dashboard_incident_list.py`](../tests/test_dashboard_incident_list.py) | 21 | Live list, shared cache, `synced_at` / age, delayed vs stale at exactly 30 s / 31 s, recovery, short error text with no URLs, search safety |
| [`test_dashboard_incidents.py`](../tests/test_dashboard_incidents.py) | 15 | Create = ServiceNow only; retry returns the first incident; lost answer then retry = one incident; `request_id` cannot inject into the query |
| [`test_dashboard_run_history.py`](../tests/test_dashboard_run_history.py) | 10 | Runs newest first with failures, retries, approvals; no payloads in the list; payload on demand; paging without gaps or repeats; cursor/limit validation. Runs in a rolled-back transaction, so nothing is left in the database |
| [`test_client.py`](../tests/test_client.py) | 11 | `correlation_id` on create, `find_incident_by_correlation` |
| [`test_approvals_api.py`](../tests/test_approvals_api.py) | 27 | Deciding from ServiceNow by incident: approve, reject, high risk needs a solution, picks the paused run, 404/409, token required |
| [`test_servicenow_status_sync.py`](../tests/test_servicenow_status_sync.py) | 11 | Status write-backs, pause work note with the brief |

---

## 9. Live Verification (demo script)

Prerequisites: `docker compose up -d --build`, dashboard at `http://localhost:8082/dashboard.html` (hard refresh after updates).

**A. ServiceNow → dashboard**
1. Create an incident in the ServiceNow form (supported category, e.g. Software).
2. Within ~13 s it appears on the dashboard. Its AI status moves Received → … → Resolved / Awaiting approval as the run progresses.
3. Change its priority or state in ServiceNow. The dashboard shows the change within ~13 s.

**B. Dashboard → ServiceNow**
1. Dashboard → **New incident** → create. The toast reports the number and whether the Business Rule queued it.
2. Open it in ServiceNow (**Open in ServiceNow ↗**). The record exists with `correlation_id = barq-dashboard-…`, and the AI fields, work notes and execution log fill in as the agent runs.
3. For a run paused for approval: the ServiceNow work note shows the brief. Approve in ServiceNow (or on the dashboard) and the run resumes; the other side shows the decision.

**C. Historical logs**
1. Expand an incident → **Run history**. The drawer shows past runs with their steps, failures and human decision.
2. **View log** on a step shows the full JSON.

**D. Failure indication (no loss, no duplicates)**
1. Block ServiceNow inside the API container only:
   `docker compose exec api sh -c "echo '127.0.0.1 <instance>.service-now.com' >> /etc/hosts"`
2. Within ~10 s the badge shows "ServiceNow delayed"; after 30 s the stale banner shows age + reason. The list stays complete.
3. Restore: `docker compose exec api sh -c "grep -v service-now.com /etc/hosts > /tmp/h && cat /tmp/h > /etc/hosts"`. The dashboard goes back to Live with no duplicate rows.
4. `docker compose stop api` → "API unreachable" with the last sync time; `docker compose start api` → back to Live.
5. Duplicate-safe create: send the same create twice (same `request_id`). The second answer is `already_created` with the same number.

---

## 10. Evidence (live run, 2026-10-03)

Screenshots from one live session against the dev instance `dev323650`. ServiceNow shows times in the instance time zone and the dashboard in local time (UTC+3), so the same moment appears as, for example, 09:34 in ServiceNow and 07:34 PM on the dashboard.

**Environment note.** ServiceNow sends its webhooks to the team's shared backend (ngrok), which ran the `development` build during this session. Sections A, B and E were captured with the dashboard pointed at that backend, so they show real AI runs. Sections C and D were captured against a local build of this branch, which adds `synced_at`, the run history and the failure states. That build does not receive the instance's webhooks, so recent incidents appear there as "Not sent to AI".

### A. ServiceNow → dashboard (INC0010395)

**1. Before:** the incident is created in the ServiceNow form. The AI Model Name / Agent Version are still empty.
![INC0010395 created in ServiceNow](images/s4.4/01-sn-created.png)

**2.** It is at the top of the ServiceNow incident list (State New, Network)…
![ServiceNow incident list](images/s4.4/02-sn-list.png)

**3.** …and appears on the dashboard within seconds, **In progress** at the Retrieve stage.
![INC0010395 in progress on the dashboard](images/s4.4/03-dash-in-progress.png)

**4. After:** the agent wrote back to ServiceNow: AI Model Name, Agent Version, AI Resolution and AI Suggestion.
![INC0010395 AI fields in ServiceNow](images/s4.4/04-sn-ai-fields.png)

**5. Matching execution log in ServiceNow:** one `auto_resolve` row, status `succeeded`, execution ID `95e523db-94ab-44a2-94b9-1b77a2d594ab`.
![AI execution log list for INC0010395](images/s4.4/05-sn-exec-log-list.png)
![AI execution log record](images/s4.4/06-sn-exec-log-record.png)

**6.** The dashboard shows the same run: **Succeeded**, AI processing state **Complete** (read from ServiceNow), the **same execution ID** `95e523db…`, ServiceNow write `written`, and the same diagnosis and resolution.
![INC0010395 succeeded on the dashboard](images/s4.4/07-dash-succeeded.png)

### B. Dashboard → ServiceNow (INC0010396)

**1.** A new incident is filled in on the dashboard. Categories come live from ServiceNow.
![New incident form](images/s4.4/08-dash-create.png)

**2.** The dashboard reports **INC0010396 created in ServiceNow** and then **passed the ServiceNow Business Rule and was queued**. The run starts (In progress, AI processing state In Progress).
![INC0010396 created and queued](images/s4.4/09-dash-created-queued.png)

**3. Before:** the record exists in ServiceNow (caller AI Integration, Hardware) with empty AI fields.
![INC0010396 in ServiceNow](images/s4.4/10-sn-record.png)

**4. After:** the dashboard shows **Succeeded** in 52 s, with diagnosis and resolution…
![INC0010396 succeeded on the dashboard](images/s4.4/11-dash-succeeded.png)

**5.** …and the same resolution is written into ServiceNow.
![INC0010396 AI fields in ServiceNow](images/s4.4/12-sn-ai-fields.png)

### C. Historical execution logs (INC0010305)

**1.** **Run history** lists every run of the incident: status, start, duration, execution ID, retries, model, the **human decision** (approved by Hady), and each step: three crash recoveries, the pause for review, the resume after the human decision, and the final result.
![Run history drawer](images/s4.4/13-history-drawer.png)

**2.** **View log** loads one step's full payload on demand.
![Step log payload](images/s4.4/14-history-step-log.png)

### D. Failure indication, no data loss, no duplicates

**1.** ServiceNow is made unreachable for the API (hosts entry). Within the 30 s window the list stays on screen and the badge says **ServiceNow delayed**.
![ServiceNow delayed](images/s4.4/18-servicenow-delayed.png)

**2.** Past 30 s the badge says **ServiceNow stale** and the banner gives the age and the reason: *"Showing ServiceNow data from 08:01:21 PM (32s old). Last refresh failed: ServiceNow could not be reached (connection refused). Retrying automatically."* The same five incidents are still listed.
![ServiceNow stale banner](images/s4.4/19-servicenow-stale-banner.png)

**3.** With the API itself stopped, the badge says **API unreachable** and the banner gives the last sync time; the list is kept.
![API unreachable](images/s4.4/20-api-unreachable.png)

**4. Duplicate-safe create:** the same create request (same `request_id`) is sent twice. The first answer is `created` and the second is `already_created`, both for **INC0010398**, so only one incident exists in ServiceNow.
![Same request twice, one incident](images/s4.4/21-no-duplicate.png)

### E. High-risk incident handed to a human (INC0010397)

**1.** A high-risk incident (production payroll database down after a suspected breach) is created from the dashboard.
![High-risk incident form](images/s4.4/15-dash-create-high-risk.png)

**2.** The risk node stops the run before retrieval. It waits on the **Approvals** page with the brief: classified high risk, no action drafted.
![Paused for approval](images/s4.4/16-approvals-paused.png)

**3.** In ServiceNow, the incident has **Human Review Required** set. The reviewer can enter a Human Solution and decide with the **Approval** / **Reject** buttons on the form.
![Human Governance tab in ServiceNow](images/s4.4/17-sn-human-governance.png)

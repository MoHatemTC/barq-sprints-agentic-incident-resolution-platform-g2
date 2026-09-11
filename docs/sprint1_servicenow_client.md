# ServiceNow Table API Client "S1.5"

OAuth-authenticated client for the AI Incident Orchestrator. Every later sprint
reaches ServiceNow through this module.

## Setup

Before running anything, copy the environment template and fill it in:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `SERVICENOW_INSTANCE_URL` | e.g. `https://devXXXXXX.service-now.com` |
| `SERVICENOW_OAUTH_CLIENT_ID` | From the OAuth Application Registry entry |
| `SERVICENOW_OAUTH_CLIENT_SECRET` | Same entry |
| `SERVICENOW_OAUTH_USERNAME` | Integration user, never an admin account |
| `SERVICENOW_OAUTH_PASSWORD` | Integration user password |
| `INCIDENT_SYS_ID` | Test incident ID from URL, required only for integration tests |

`config.py` raises on startup naming any variable that is missing or blank,
rather than failing later with an error. `.env` is gitignored and no credential appears in code, logs or ServiceNow records

## Module layout

| File | Responsibility |
|---|---|
| `config.py` | Environment loading, table names, AI field map, log status values |
| `auth.py` | OAuth token fetch, cache, expiry detection, invalidation |
| `exceptions.py` | exceptions and status to exception mapping |
| `client.py` | The four methods, request plumbing, 401 retry |

## Methods

| Method | HTTP | Notes |
|---|---|---|
| `get_incident(sys_id)` | GET | Returns all fields; values arrive as strings |
| `update_incident(sys_id, fields)` | PATCH | Logical field names, validated before send |
| `add_work_note(sys_id, note)` | PATCH | Journal field , appends "patchs", never overwrites "put" |
| `write_execution_log(...)` | POST | Never raises, returns `None` on failure |

## Error taxonomy

| Status | Meaning | Retryable | Exception | Log state |
|---|---|---|---|---|
| 401 | Token expired or invalid | Yes need refresh, retry once | `ServiceNowAuthError` | failed |
| 403 | Integration user lacks permission | No | `ServiceNowPermissionError` | blocked |
| 404 | Record missing **or** read restricted | No | `ServiceNowNotFoundError` | failed |
| 409 | Record changed While the request | No | `ServiceNowConflictError` | failed |
| 422 | Payload rejected | No | `ServiceNowValidationError` | failed |
| 5xx | ServiceNow unavailable | Yes | `ServiceNowServerError` | failed |

Every request carries an explicit timeout (`SERVICENOW_TIMEOUT`, default 30s),
adjustable in `.env`.

## Token lifecycle

Password grant against `/oauth_token.do`. Tokens live roughly 30 minutes,
cached in memory and treated as stale 60 seconds early so a request cannot
expire in flight.

A 401 despite a cached token invalidates it, fetches a new one, and retries
the request exactly once.

The refresh token is not used — the password grant is re-run instead, which is
simpler and equally valid here.

## Log-write latency

I Measured latency by script against the live PDI, 20 sequential writes results:

| min | median | p95 | max |
|---|---|---|---|
| 821 ms | 977 ms | 1624 ms | 2041 ms |

At roughly one second per write, logging is reliable but not free. A graph run
recording five attempts would spend ~5s on audit alone

so Sprint 3 should consider batching or asynchronous logging rather than blocking the workflow on
each write

>> Note these figures come from a shared free PDI and would improve on
a dedicated instance

## Observed platform behaviour

What I founded from testing against the live instance :

1- **A 200 does not prove a write landed.** PATCH requests including fields the
integration user cannot write return 200 with those fields silently unwritten.
`ai_confidence` behaved this way while `ai_classification` and `ai_suggestion`
in the same payload wrote correctly , The only defence is reading back

2- **404 is ambiguous.** ServiceNow returns *"Record doesn't exist or ACL restricts
the record retrieval"* for both cases, deliberately, A 404 cannot be read as
proof a record is absent

3- **Reference fields return objects, not strings.** `row["incident"]` is
`{"value": ..., "link": ...}`. Callers must read `["value"]`

4- **No referential validation on insert.** An execution log row accepts an
unparseable incident reference and stores it verbatim, Valid sys_ids are the
caller's responsibility

## Known limitations

`add_work_note` is not idempotent, Work notes are a journal field, so each call
appends a new entry this is **platform behaviour that cannot be changed** Callers must
not blindly retry work notes after a timeout.

Integration tests do not delete the execution log rows they create, This is
deliberate: an audit trail should not be erasable by its own test suite Rows
accumulate without affecting repeatability

## Running the tests

Everything:

```bash
pytest tests/ -v
```

A single file:

```bash
pytest tests/test_auth.py -v
pytest tests/test_client.py -v
pytest tests/test_exceptions.py -v
pytest tests/test_integration.py -v
```

Unit tests only, no credentials needed:

```bash
pytest tests/ -v -k "not integration"
```

Unit tests run without credentials. Integration tests need a filled-in `.env`
and skip automatically when `INCIDENT_SYS_ID` is unset.

----

all this task done by @bassanthossamxx
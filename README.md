# BARQ Agentic Incident Resolution Platform

[![CI](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g2/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g2/actions/workflows/ci.yml)

An AI agent that resolves ServiceNow incidents on its own when it is safe to, and hands them to a
human when it is not.

When an incident is created or updated in ServiceNow, the platform receives it, looks up the
answer in the BARQ IT knowledge base, drafts a diagnosis and a resolution, checks that draft
against the evidence and against safety rules, and writes the result back to the incident.
High-risk or uncertain incidents stop for a human reviewer first, and every solution a human
approves becomes a new knowledge-base article, so the agent can reuse it next time.

**Status:** Sprint 4 (testing, fixes and demo preparation). Group 2, BARQ x Sprints.

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [The agent](#the-agent)
- [Safety and reliability](#safety-and-reliability)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Running the platform](#running-the-platform)
- [The web console](#the-web-console)
- [API reference](#api-reference)
- [Testing, evaluation and CI](#testing-evaluation-and-ci)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Contributing](#contributing)

---

## What it does

- **Resolves incidents end to end.** It classifies the incident, rates its risk, retrieves
  evidence, diagnoses the cause, writes a cited resolution and posts it to the ServiceNow
  incident. Everything the AI writes, including the Resolution Information tab, goes to the
  incident.
- **Keeps a human in the loop.** High-risk incidents stop *before* any retrieval or model spend.
  Low confidence, a blocked draft or a failed critic also stop for a reviewer. The reviewer sees
  a short approval brief, approves once, and the run continues from where it paused.
- **Learns from people.** An approved human solution is published to the ServiceNow knowledge
  base as a `KBHR-` article and indexed, so the next matching incident reuses it without calling
  the model again.
- **Grounds every answer.** Hybrid search (dense + BM25, fused with RRF, then a cross-encoder
  reranker) over the ServiceNow knowledge base and the BARQ IT Service Desk Manual.
- **Never loses an incident.** It uses idempotent intake, checkpointed runs that survive a
  worker crash, retries with backoff, a dead-letter queue, and a sweep that catches incidents
  whose webhook never arrived.

## How it works

```mermaid
flowchart LR
    SN[(ServiceNow<br/>incident)] -- "Business Rule<br/>POST + Bearer token" --> API[FastAPI<br/>webhook]
    API -- "persist event<br/>(idempotent on event_id)" --> PG[(PostgreSQL)]
    API -- "202 Accepted,<br/>enqueue" --> RQ[(Redis)]
    SWEEP[Delivery sweep] -. "missed webhooks" .-> RQ
    RQ --> CON[Consumer] --> CEL[Celery worker]
    CEL --> AG[LangGraph agent]
    AG <--> QD[(Qdrant<br/>hybrid index)]
    AG <--> LLM[LiteLLM<br/>Gemini]
    AG -- "checkpoints" --> PG
    AG -- "ToolRegistry<br/>(OAuth)" --> SN
    AG -. traces .-> LF[Langfuse]
    UI[Web console] <--> API
```

1. **Intake.** The *AI Eligibility Check* Business Rule in ServiceNow posts eligible incidents to
   `POST /api/v1/webhook/incident` with a shared Bearer token and `contract_version: "v1"`.
   The API stores the event (a duplicate `event_id` is acknowledged and ignored), pushes it to
   Redis and answers `202` immediately.
2. **Dispatch.** The consumer moves events from Redis to Celery. A delivery sweep in the same
   process asks ServiceNow for eligible *Pending* incidents we never received, for example while
   the tunnel was down, and feeds them into the same path.
3. **Processing.** A Celery worker runs the LangGraph agent. The incident's AI Processing State
   moves to *In Progress*, and every step is checkpointed in PostgreSQL.
4. **Write-back.** The agent writes to ServiceNow only through the ToolRegistry (OAuth), then
   records an execution-log receipt so a retried run never writes twice.
5. **Review.** When a run needs a human, it pauses. A reviewer decides in the Approvals page or
   through the API, and the same run resumes from its checkpoint.

## The agent

```mermaid
flowchart TD
    load --> validate
    validate -- "invalid / out of scope" --> prepare_review
    validate --> classify --> determine_risk
    determine_risk -- "high risk" --> prepare_review
    determine_risk -- "normal risk" --> retrieve
    retrieve -- "no evidence" --> prepare_review
    retrieve -- "KB cache hit" --> safety_check
    retrieve --> diagnose --> generate --> verify_evidence
    verify_evidence -- "critic rejects, retries left" --> generate
    verify_evidence -- "critic exhausted" --> prepare_review
    verify_evidence -- "critic passes" --> safety_check
    safety_check --> confidence_check
    confidence_check -- "blocked / confidence < 0.6" --> prepare_review
    confidence_check -- "confident" --> act
    prepare_review --> interrupt
    interrupt -- "approved high risk:<br/>enrich with KB evidence" --> retrieve
    interrupt -- "approved / rejected" --> act
    act -- "approved human solution" --> knowledge_capture
    act --> done([end])
    knowledge_capture --> done
```

| Stage | What happens |
|---|---|
| `load`, `validate` | Load the incident, screen it for prompt injection, redact sensitive data, reject invalid tickets |
| `classify`, `determine_risk` | Category and risk. **Risk is decided before retrieval**, so high-risk incidents cost no search or generation. |
| `retrieve` | Hybrid search with metadata filters (published, non-restricted content only). A strong match on a human-approved `KBHR-` article is reused directly. |
| `diagnose` → `generate` → `verify_evidence` | Three agents: a diagnosis agent, a resolution agent that cites its sources, and a critic that checks the draft against the evidence and sends it back for revision |
| `safety_check`, `confidence_check` | Output guardrails (action allowlist, content screening, schema), then the confidence floor (0.6) |
| `prepare_review`, `interrupt` | Build the approval brief and pause the run until a human decides |
| `act` | Write the outcome to ServiceNow exactly once, plus a work note with the reviewer's decision |
| `knowledge_capture` | Publish the approved human solution as a KB article and index it in Qdrant |

**Human review rules**

- **High risk at the risk stage:** the reviewer approves *and* provides a solution. The agent then
  enriches it with KB evidence before writing.
- **Stopped later** (guardrail block, low confidence, critic exhausted): the reviewer sees what the
  agent has so far. **One approval per run, never two.**
- If, after approval, the draft is blocked or the critic gives up, `act` writes the reviewer's
  approved solution instead of the AI draft.

Design details: [graph design](docs/sprint3_graph_design.md),
[multi-agent design](docs/sprint3_multi_agent_design.md),
[human-in-the-loop](docs/sprint3_hitl_design.md),
[knowledge capture](docs/sprint3_knowledge_capture_design.md).

## Safety and reliability

| Concern | How it is handled |
|---|---|
| Unsafe writes | Every ServiceNow call goes through the **ToolRegistry** (`src/agent/tools/registry.py`), the single source of truth for permission classes: `READ`, `LOW_RISK_WRITE`, `HIGH_RISK`. A CI test fails if agent code touches the ServiceNow client directly. See [tool registry](docs/sprint3_tool_registry.md). |
| Prompt injection and data leaks | Input screening and redaction before the model; output validation before any write. See [guardrail design](docs/sprint3_guardrail_design.md). |
| Duplicate events | Idempotency on `event_id`; a duplicate still gets `202`. |
| Worker crash mid-run | LangGraph checkpoints in PostgreSQL; the retry resumes, and the execution-log receipt makes the ServiceNow write exactly-once. See [recovery design](docs/sprint3_recovery_design.md). |
| Transient failures | Celery retries with exponential backoff, then a dead-letter queue you can inspect and replay. The incident is marked *Failed* in ServiceNow with a work note explaining why. |
| Missed webhooks | The delivery sweep (`DELIVERY_SWEEP_INTERVAL_SECONDS`, default 60, `0` disables) |
| Auth | ServiceNow → webhook: shared Bearer token. Backend → ServiceNow: OAuth, least-privilege integration user. See [audit and identity](docs/sprint1_audit_and_identity.md). |
| Observability | Langfuse traces per node and per agent, structured JSON logs with correlation IDs, and an execution timeline in PostgreSQL |

## Tech stack

| Layer | Tools |
|---|---|
| API | FastAPI, Uvicorn, Pydantic |
| Agent | LangGraph (PostgreSQL checkpointer), LangChain, LiteLLM gateway (Gemini) |
| Retrieval | Qdrant, dense embeddings via LiteLLM, BM25 and a MiniLM cross-encoder via fastembed |
| Work queue | Redis, Celery |
| State | PostgreSQL, SQLAlchemy, Alembic |
| Integration | ServiceNow Table API (OAuth), Business Rule + system properties, update sets |
| Observability | Langfuse, OpenTelemetry |
| Ops | Docker Compose, GitHub Actions, GHCR |
| UI | Static HTML/JS console (no build step) |

## Project structure

```
├── src/
│   ├── api/            # FastAPI app and routers (webhook, approvals, executions, dashboard, kb, dlq, eval)
│   ├── agent/          # LangGraph graph, nodes, guardrails, ToolRegistry, knowledge capture
│   ├── workers/        # Celery tasks, retry policy, DLQ, consumer, delivery sweep
│   ├── retrieval/      # PDF parsing, document extractors (incl. OCR), KB publishing, ingestion, hybrid search
│   ├── servicenow/     # Table API client (OAuth)
│   ├── db/             # SQLAlchemy models and services
│   ├── orchestrator/   # workflow services
│   └── observability/  # Langfuse tracing
├── servicenow/         # ServiceNow update sets (.xml): field model, Business Rule
├── migrations/         # Alembic migrations
├── ui/                 # web console: dashboard, approvals, knowledge base
├── eval/               # retrieval evaluation set, ablation, CI gate
├── scripts/, tools/    # KB checks, snapshots, permission checks
├── data/               # BARQ IT Service Desk Manual, KB snapshot, stressor corpus
├── tests/              # unit and integration tests
├── docs/               # design docs and reports, one per task
├── .github/            # CI/CD workflows
├── docker-compose.yml  # qdrant, postgres, redis, api, worker, consumer, ui
├── run_worker.py       # Celery worker entry point
└── run_consumer.py     # Redis consumer + delivery sweep entry point
```

## Setup

### Prerequisites

- Python 3.11+ (the Docker image and CI use 3.12)
- Git
- Docker Desktop, running
- Access to the team's ServiceNow instance, LiteLLM and Langfuse (ask the team for credentials)

**First time on a machine:** do steps 1–9 once. **After that:** see [Running the platform](#running-the-platform).

### 1. Clone the repository

```bash
git clone https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g2.git
cd barq-sprints-agentic-incident-resolution-platform-g2
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv
```

```bash
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate
```

**Or with conda** (recommended on Windows; avoids some native dependency issues):
```bash
conda create -n barq-orch python=3.12 -y
conda activate barq-orch
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Fill in `.env` locally. It is git-ignored and must **never** be committed; only `.env.example`
(empty values) is tracked. For code running **on your machine**, hosts are `localhost`
(e.g. `DATABASE_URL=postgresql://app:app@localhost:5432/orchestrator`,
`QDRANT_URL=http://localhost:6333`). The containers override these with their own service names,
so the same `.env` works for both.

| Group | Main variables |
|---|---|
| Model gateway | `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `LLM_MODEL`, `LITELLM_EMBEDDING_MODEL` |
| ServiceNow | `SERVICENOW_INSTANCE_URL`, `SERVICENOW_OAUTH_*`, `SERVICENOW_KB_*` |
| Webhook | `WEBHOOK_AUTH_TOKEN` (must match the ServiceNow system property), `DELIVERY_SWEEP_INTERVAL_SECONDS` |
| Stores | `DATABASE_URL`, `PG_CONN_STRING`, `REDIS_*`, `QDRANT_URL`, `QDRANT_COLLECTION_NAME` |
| Retrieval | `RETRIEVAL_MODE` (`dense` / `hybrid` / `hybrid_rerank`), `RETRIEVAL_TOP_K`, `RERANK_MODEL`, `ALLOWED_WORKFLOW_STATES`, `BLOCKED_SECURITY_LEVELS` |
| Worker | `CELERY_*` (queues, concurrency, time limits, retries) |
| Tracing | `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` |

`.env.example` documents every variable.

### 5. Build the image and start the stack

```bash
docker compose up -d --build
docker compose ps          # qdrant / postgres / redis should become "healthy"
```

### 6. Create the database tables

```bash
python -m alembic upgrade head
```

Run it again whenever you pull new migrations.

### 7. Knowledge base: publish to ServiceNow and index into Qdrant

ServiceNow is the source of truth for articles; Qdrant is the search index.

```bash
python -m scripts.verify_kb             # is the BARQ manual already in ServiceNow? -> "RESULT: ALL GOOD"
python -m src.retrieval.publish_kb      # only if verify_kb reports missing articles (--dry-run to preview)
python -m src.retrieval.ingest sync     # copy ServiceNow articles into Qdrant (or "Update Vector DB" in the UI)
```

> [!WARNING]
> The ServiceNow instance is shared by the team. Do not run `publish_kb` just to check something,
> and never run `scripts/reset_kb.py` without agreeing with the team: it deletes every article in
> the knowledge base.

### 8. Connect ServiceNow to your API

The Business Rule has to reach your API over a public URL (e.g. an ngrok tunnel to port 8000).

1. Import the update sets in `servicenow/ai_incident_orchestrator/` if the instance does not have them yet.
2. In ServiceNow, set the system properties:
   - `x_2215689_ai_inc_0.webhook_url`: your public base URL, e.g. `https://xxxx.ngrok-free.app` (no trailing slash)
   - `x_2215689_ai_inc_0.webhook_token`: the same value as `WEBHOOK_AUTH_TOKEN` in `.env`

A new tunnel URL only needs the property changed, not the script. If the tunnel is down, the
delivery sweep picks the missed incidents up once the consumer is running.

### 9. Verify the setup

```bash
python scripts/verify_permissions.py
pytest -m "not integration"
```

See [Testing](#testing-evaluation-and-ci) for the integration tests.

## Running the platform

Start Docker Desktop first. Commands are the same on Windows (PowerShell) and macOS/Linux unless noted.

### Option A: everything in Docker (normal use)

```bash
docker compose up -d                  # add --build after pulling code changes
docker compose ps                     # all services "Up"
docker compose logs -f api            # follow logs (also: worker, consumer); Ctrl+C stops following only
```

| What | URL |
|---|---|
| Web console | http://localhost:8082/dashboard.html |
| API health | http://localhost:8000/health and http://localhost:8000/ready |
| API docs (try every endpoint) | http://localhost:8000/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |

### Option B: code locally, infrastructure in Docker (development and debugging)

```bash
docker compose up -d qdrant postgres redis
```

Then one terminal per process (virtual environment activated):

```bash
uvicorn src.api.app:app --reload --port 8000   # API
python run_worker.py                           # Celery worker
python run_consumer.py                         # Redis consumer + delivery sweep
python -m http.server 5500 --directory ui      # web console on http://localhost:5500
```

Do not run Option A's `api` / `worker` / `consumer` containers at the same time: port 8000 and
the queue would be shared.

### Stopping

```bash
docker compose down        # stop containers, keep data
docker compose down -v     # stop and DELETE all data: the next start is a fresh setup (repeat steps 6-7)
```

Processes started in a terminal (Option B): `Ctrl+C`.

### Useful commands

| Command | What it does |
|---|---|
| `python -m scripts.verify_kb` | Read-only check that ServiceNow matches the PDF (count, duplicates, state, text, category) |
| `python -m src.retrieval.publish_kb --dry-run` | Show what publishing the manual would create or update |
| `python -m src.retrieval.ingest sync` | Sync Qdrant with ServiceNow (adds, updates, deletes) |
| `python -m src.retrieval.manual_parser` | List the sections parsed from the PDF |
| `python -m scripts.export_kb_snapshot` | Freeze the published KB into `data/kb_dataset.json` for the eval gate |

## The web console

A static console in `ui/` that talks to the API (default `http://localhost:8000`; change it in the
console's settings).

| Page | What you can do |
|---|---|
| **Dashboard** (`dashboard.html`) | Latest ServiceNow incidents with their AI run: status, live node progress, duration, a link to the incident form. Search, filter (including *Not sent to AI*), and create a test incident in ServiceNow. |
| **Approvals** (`approvals.html`) | Paused runs with their approval brief. Approve (with a solution for high risk) or reject, with a rationale. |
| **Knowledge base** (`kb.html`) | Articles and their index status. Add an article or upload a `.txt` / `.md` file, then *Update Vector DB*. |

"Create incident", "Add article" and "Update Vector DB" act on the **real** ServiceNow instance and LiteLLM.

## API reference

Full interactive docs at http://localhost:8000/docs (schema also in [`openapi.json`](openapi.json)).

| Area | Endpoint | Purpose |
|---|---|---|
| Intake | `POST /api/v1/webhook/incident` | ServiceNow events (Bearer token, contract v1). See [event contract](docs/event_contract_v1.md). |
| Health | `GET /health`, `GET /ready` | Liveness and readiness |
| Executions | `GET /api/v1/executions/{id}`, `GET /api/v1/executions/{id}/trace` | One run and its step timeline |
| | `GET /api/v1/incidents/{sys_id}/executions` | All runs for an incident |
| Approvals | `GET /api/v1/approvals`, `GET /api/v1/approvals/{id}` | Pending reviews and their brief |
| | `POST /api/v1/approvals/{id}/decide` | Approve or reject; resumes the paused run |
| Dead letters | `GET /api/v1/dlq`, `POST /api/v1/dlq/{event_id}/replay` | Inspect and replay failed events |
| Dashboard | `GET /api/v1/dashboard/incidents`, `POST /api/v1/dashboard/incidents` | List (paged, searchable) and create incidents |
| | `GET /api/v1/dashboard/executions`, `GET /api/v1/dashboard/incident-categories`, `GET /api/v1/dashboard/incidents/{sys_id}/events` | Recent runs, categories, webhook events per incident |
| | `DELETE /api/v1/dashboard/incidents/{sys_id}`, `POST /api/v1/dashboard/kb-sync` | Delete a test incident; sync Qdrant with ServiceNow |
| Knowledge base | `GET /api/v1/kb/articles`, `POST /api/v1/kb/articles`, `POST /api/v1/kb/articles/upload` | List, add and upload articles |
| Evaluation | `GET /api/v1/eval/results`, `POST /api/v1/eval/run` | Retrieval evaluation results |
| Config | `GET /api/v1/config` | Database and Redis hosts and ports the API is using |

## Testing, evaluation and CI

Tests are split by what they need; the list lives in `tests/conftest.py`.

```bash
pytest -m "not integration"      # unit tests: no services needed
pytest -m integration            # needs Postgres, Redis and Qdrant running, and `alembic upgrade head`
```

- The PDF extractor tests (`tests/test_extractors_*.py`, `tests/test_ingest_stressors.py`) are slow (~3 min).
- The live ServiceNow tests (`tests/test_integration.py`, `tests/test_workflow_servicenow.py`)
  skip themselves unless `INCIDENT_SYS_ID` / `INCIDENT_NUMBER` point at a dedicated test incident.
- A new test file that uses the real database must be added to the integration list in
  `tests/conftest.py`; otherwise it fails in CI's unit job.

**Retrieval evaluation gate.** It scores `dense`, `hybrid` and `hybrid_rerank` on a fixed
evaluation set and fails if hit rate, MRR or recall drop below `eval/thresholds.json`:

```bash
python -m src.retrieval.ingest local   # index the KB snapshot into a fresh Qdrant
python eval/ablation.py --k 5          # score the three modes
python eval/gate.py                    # compare against the floors
```

**CI/CD (GitHub Actions)**

| Workflow | Runs | What it checks |
|---|---|---|
| `ci.yml` | every PR; pushes to `main` / `development` | Lint (ruff), unit tests, integration tests (Postgres + Redis + Qdrant), eval gate, Docker build + Compose smoke test; on `main`, publishes the image to GHCR |
| `extractors.yml` | PRs that touch extractors / PDFs; pushes to `main` | Slow PDF extractor tests |
| `live-servicenow.yml` | PRs that touch ServiceNow code; pushes to `main`; nightly | Tests against the real instance |

Setup, secrets and how to run each job locally: [docs/CI_CD.md](docs/CI_CD.md).

## Troubleshooting

- **Port already in use** (5432, 6379, 6333 or 8000). Another PostgreSQL / Redis on your machine
  is using it. Publish ours on other ports with a local `docker-compose.override.yml` (Compose loads
  it automatically; add it to `.git/info/exclude` so it is never committed):

  ```yaml
  services:
    postgres:
      ports: !override ["5433:5432"]
    redis:
      ports: !override ["6380:6379"]
  ```

  Then point local runs at those ports, e.g. in PowerShell:

  ```powershell
  $env:DATABASE_URL="postgresql://app:app@localhost:5433/orchestrator"
  $env:PG_CONN_STRING="postgresql://app:app@localhost:5433/orchestrator"
  $env:POSTGRES_PORT="5433"; $env:REDIS_HOST="localhost"; $env:REDIS_PORT="6380"
  $env:REDIS_URL="redis://localhost:6380/0"; $env:CELERY_BROKER_URL="redis://localhost:6380/0"
  ```

- **`relation "executions" does not exist`** or a missing column: run `python -m alembic upgrade head`.
- **`password authentication failed for user "app"`**: you are reaching a different PostgreSQL on
  the same port (see above).
- **Incidents stay *Pending* in ServiceNow**: check the webhook system properties (step 8), that
  the tunnel is up, and that the consumer is running (it also runs the delivery sweep).
- **Webhook returns 401 / 422**: the token does not match `WEBHOOK_AUTH_TOKEN`, or the payload has
  no `contract_version: "v1"`.
- **UI shows "HTTP …" / network errors**: the API is not running on port 8000, or the console's
  API URL setting is wrong.
- **`'&&' is not a valid statement separator`** in PowerShell: run the commands one per line.

## Documentation

Design docs and reports live in [`docs/`](docs/), one per task.

| Topic | Documents |
|---|---|
| ServiceNow integration | [Field model](docs/sprint1_field_model.md) · [Table API client](docs/sprint1_servicenow_client.md) · [Audit and identity](docs/sprint1_audit_and_identity.md) · [Event contract v1](docs/event_contract_v1.md) · [Eligibility evidence](docs/sprint2_eligibility_evidence.md) |
| Platform | [API surface](docs/sprint2_api_surface.md) · [State schema](docs/sprint2_state_schema.md) · [Worker topology](docs/sprint2_worker_topology.md) · [Workflow reliability tests](docs/workflow_reliability_tests.md) |
| Retrieval | [Corpus design](docs/sprint1_corpus_design.md) · [Index spec](docs/sprint1_index_spec.md) · [Ingestion](docs/sprint2_ingestion_design.md) · [Hybrid retrieval](docs/sprint2_retrieval_report.md) · [Evaluation matrix](docs/sprint2_evaluation_matrix_report.md) · [RAG hardening](docs/sprint2_rag_hardening_readme.md) ([report](docs/sprint2_rag_hardening_report.md)) |
| Agent | [Tracing and agent init](docs/sprint2_tracing_and_agent.md) · [Graph design](docs/sprint3_graph_design.md) · [Multi-agent design](docs/sprint3_multi_agent_design.md) · [Agent topology](docs/sprint3_agent_topology.md) |
| Safety | [Tool registry](docs/sprint3_tool_registry.md) · [Guardrails](docs/sprint3_guardrail_design.md) · [Human-in-the-loop](docs/sprint3_hitl_design.md) · [Crash recovery](docs/sprint3_recovery_design.md) · [Knowledge capture](docs/sprint3_knowledge_capture_design.md) |
| Operations | [CI/CD](docs/CI_CD.md) · [Verification log](docs/verification_log.md) · [UI and system evaluation](docs/ui_and_system_evaluation_report.md) |

## Contributing

### Branches and pull requests

Never commit directly to `main` or `development`.

1. Branch off the latest `development`:

   ```bash
   git checkout development
   git pull origin development
   git checkout -b <type>/<short-name>      # e.g. feat/work-notes, fix/sweep-timeout
   ```

2. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`) and push:

   ```bash
   git push -u origin <your-branch>
   ```

3. Open a pull request into `development`, wait for CI to go green, and get a review.
4. `development` is merged into `main` for releases; a push to `main` publishes the Docker image.

### Rules

- **Never commit `.env`** or any secret.
- **Every ServiceNow call goes through the ToolRegistry.** Register new tools with the right
  permission class; do not import the ServiceNow client in agent code.
- **Database changes need a new Alembic migration.** Never edit an existing one.
- **Tests that need the database** go in the integration list in `tests/conftest.py`.
- **New packages:** install them, then regenerate `requirements.txt` and commit it with the change:

  ```bash
  pip install <package>
  pip freeze > requirements.txt
  ```

  After pulling a change to `requirements.txt`, run `pip install -r requirements.txt`.

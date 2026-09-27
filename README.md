# barq-sprints-agentic-incident-resolution-platform-g2
A production-grade ServiceNow incident resolution platform featuring LangGraph orchestration, hybrid RAG, safety guardrails, observability, and human-in-the-loop approval.

**Current sprint:** Sprint 3

## Project Structure

```
├── docker-compose.yml     # qdrant, postgres, redis + api, worker, consumer
├── Dockerfile             # image for api / worker / consumer
├── .env.example           # every setting, empty values (copy to .env)
├── alembic.ini, migrations/   # database schema
├── run_worker.py          # Celery worker entry point
├── run_consumer.py        # Redis event consumer entry point
├── ui/                    # web console: dashboard.html, kb.html
├── data/
│   └── BARQ_IT_Service_Desk_Manual_Ed5.1.pdf   # the knowledge-base source
├── src/
│   ├── api/               # FastAPI app and routers
│   ├── agent/             # LangGraph agent
│   ├── workers/           # Celery tasks, retries, DLQ
│   ├── db/                # SQLAlchemy models and services
│   ├── retrieval/         # PDF parsing, publishing, ingestion, hybrid search
│   └── servicenow/        # Table API client
├── scripts/               # KB publish checks and admin helpers
├── eval/                  # retrieval evaluation
├── servicenow/            # ServiceNow update sets (.xml)
├── tests/
└── docs/
```

## Setup

### Prerequisites

- Python 3.11+ (the Docker image uses 3.12)
- Git
- Docker Desktop (running)

**First time on a machine:** do steps 1–8 below once.
**After that:** see [Running the platform](#running-the-platform).

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

**Option — conda (recommended on Windows, avoids some native dependency issues, e.g. with torch):**
```bash
conda create -n barq-orch python=3.11 -y
conda activate barq-orch
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the template and fill in your values locally. `.env` is git-ignored and must never be committed — only `.env.example` (with empty values) is tracked.

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Ask the team for the ServiceNow, LiteLLM and Langfuse values. For running code
**on your machine**, hosts in `.env` are `localhost` (e.g.
`DATABASE_URL=postgresql://app:app@localhost:5432/orchestrator`,
`QDRANT_URL=http://localhost:6333`). The Docker containers override these with
their own service names, so the same `.env` works for both.

### 5. Build the image and start the stack

```bash
docker compose up -d --build
docker compose ps          # qdrant / postgres / redis should become "healthy"
```

### 6. Create the database tables

```bash
python -m alembic upgrade head
```

### 7. Knowledge base: publish to ServiceNow and index into Qdrant

ServiceNow is the source of truth for articles; Qdrant is the search index.

```bash
python -m scripts.verify_kb             # is the BARQ manual already in ServiceNow? -> "RESULT: ALL GOOD"
python -m src.retrieval.publish_kb      # only if verify_kb reports missing articles (--dry-run to preview)
python -m src.retrieval.ingest sync     # copy ServiceNow articles into Qdrant (or "Update Vector DB" in the UI)
```

The ServiceNow instance is shared by the team: do not run `publish_kb` just to
check something, and never run `scripts/reset_kb.py` without agreeing with the team
(it deletes every article in the knowledge base).

### 8. Verify the setup

```bash
python scripts/verify_permissions.py
pytest --ignore=tests/load
```

Integration tests need the stack from step 5 running and a valid ServiceNow login in `.env`.

## Running the platform

Every time after the first setup. Commands are the same on Windows (PowerShell) and macOS/Linux
unless noted. Start Docker Desktop first.

### Option A: everything in Docker (normal use)

```bash
docker compose up -d                  # add --build after pulling code changes
docker compose ps                     # all 6 services "Up"
docker compose logs -f api            # follow logs (also: worker, consumer); Ctrl+C stops following only
```

| What | URL |
|---|---|
| API health | http://localhost:8000/health and http://localhost:8000/ready |
| API docs (try every endpoint) | http://localhost:8000/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |

Then start the UI (Option C).

### Option B: run the code locally, infrastructure in Docker (for development and debugging)

```bash
docker compose up -d qdrant postgres redis
```

Then one terminal per process (virtual environment activated):

```bash
uvicorn src.api.app:app --reload --port 8000   # API
python run_worker.py                           # Celery worker
python run_consumer.py                         # Redis event consumer
```

Do not run Option A's `api` / `worker` / `consumer` containers at the same time
(port 8000 and the queue would be shared).

### Option C: the UI

The UI is static HTML that calls the API at `http://localhost:8000`, so start the
API first (Option A or B):

```bash
python -m http.server 5500 --directory ui
```

- http://localhost:5500/dashboard.html: recent executions, create a test incident
- http://localhost:5500/kb.html: knowledge-base articles and their index status, add or upload an article, "Update Vector DB"

"Create incident", "Add article" and "Update Vector DB" call the real ServiceNow
instance and LiteLLM.

### Stopping

```bash
docker compose down        # stop containers, keep data
docker compose down -v     # stop and DELETE all data (database, Qdrant, Redis): next start is a fresh setup (repeat steps 6-7)
```

Processes started in a terminal (Option B, the UI server): `Ctrl+C`.

### Useful commands

| Command | What it does |
|---|---|
| `python -m scripts.verify_kb` | Read-only check that ServiceNow matches the PDF (count, duplicates, state, text, category) |
| `python -m src.retrieval.publish_kb --dry-run` | Show what publishing the manual would create / update |
| `python -m src.retrieval.ingest sync` | Sync Qdrant with ServiceNow (adds, updates, deletes) |
| `python -m src.retrieval.manual_parser` | List the sections parsed from the PDF |
| `pytest --ignore=tests/load` | Unit and integration tests |

### Troubleshooting

- **Port already in use** (5432, 6379, 6333 or 8000). Another PostgreSQL / Redis on your
  machine is using it. Publish ours on other ports with a local
  `docker-compose.override.yml` (Compose loads it automatically; add it to
  `.git/info/exclude` so it is never committed):

  ```yaml
  services:
    postgres:
      ports: !override ["5433:5432"]
    redis:
      ports: !override ["6380:6379"]
  ```

  Then, for Option B and for tests, point your local runs at those ports, e.g. in PowerShell:

  ```powershell
  $env:DATABASE_URL="postgresql://app:app@localhost:5433/orchestrator"
  $env:PG_CONN_STRING="postgresql://app:app@localhost:5433/orchestrator"
  $env:POSTGRES_PORT="5433"; $env:REDIS_HOST="localhost"; $env:REDIS_PORT="6380"
  $env:REDIS_URL="redis://localhost:6380/0"; $env:CELERY_BROKER_URL="redis://localhost:6380/0"
  ```

- **`relation "executions" does not exist`**: the database is new, so run `python -m alembic upgrade head`.
- **`password authentication failed for user "app"`**: you are reaching a different PostgreSQL
  on the same port (see "Port already in use").
- **UI shows "HTTP …" / network errors**: the API is not running on port 8000.
- **`'&&' is not a valid statement separator`** in PowerShell: run the commands one per line.

## Contributing

### Dependencies

Whenever you install a new package, you **must** regenerate `requirements.txt` so everyone stays on the same versions:

```bash
pip install <package>
pip freeze > requirements.txt
```

Commit the updated `requirements.txt` with the change that needed it. Never edit it by hand.

After pulling changes that touch `requirements.txt`, re-sync your environment:

```bash
pip install -r requirements.txt
```

### Branching & pull requests

Do **not** commit directly to `main`. Every change goes through a branch and a pull request.

1. Create your own branch off `main`:

   ```bash
   git checkout main
   git pull origin main
   git checkout -b <your-branch-name>
   ```

2. Commit your work and push the branch:

   ```bash
   git add .
   git commit -m "<type>: <short description>"
   git push -u origin <your-branch-name>
   ```

3. Open a pull request against `main` and request a review.
4. Merge only after the PR is reviewed and approved.

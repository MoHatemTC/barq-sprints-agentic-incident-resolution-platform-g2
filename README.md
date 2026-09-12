# barq-sprints-agentic-incident-resolution-platform-g2
A production-grade ServiceNow incident resolution platform featuring LangGraph orchestration, hybrid RAG, safety guardrails, observability, and human-in-the-loop approval.

**Current sprint:** Sprint 1

## Project Structure

```
barq-ai-incident-orchestrator/
│
├── docker-compose.yml          # S1.4 — Qdrant + PostgreSQL + Redis
├── .env.example                # committed, empty values
├── .gitignore                  # .env must be in here
├── README.md                   # stack startup instructions
├── requirements.txt
│
├── servicenow/
│   └── ai_incident_orchestrator/       # update set .xml — S1.1/S1.2/S1.3
|       |── update_set.xml #S1.3
│       └── .gitkeep
│   └── .gitkeep
│
├── src/
│   ├── __init__.py
│   ├── servicenow/             # S1.5 — Table API client
│   │   └── __init__.py
│   └── retrieval/              # S1.4
│       ├── __init__.py
│
├── scripts/
│   └── verify_permissions.py   # S1.2
│
├── data/
│   └── coverage_matrix.csv     # S1.4 — ground truth
│
├── tests/
│   └── __init__.py             # S1.5
│
└── docs/
    |── images/ #evidence screenshots
    ├── event_contract_v1.md                # S1.3
    ├── sprint2_eligibility_evidence.md      # S1.3
    └── sprint1_servicenow_client.md        # S1.5
```

## Setup

### Prerequisites

- Python 3.11+
- Git

### 1. Clone the repository

```bash
git clone https://github.com/<org>/barq-sprints-agentic-incident-resolution-platform-g2.git
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

### 5. Start the infrastructure stack

Brings up Qdrant, PostgreSQL, and Redis (S1.4):

```bash
docker compose up -d
```

Check the containers are healthy:

```bash
docker compose ps
```

Stop the stack when you're done:

```bash
docker compose down
```

### 6. Verify the setup

```bash
python scripts/verify_permissions.py
pytest
```

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

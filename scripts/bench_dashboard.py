"""S4.4 latency benchmark for the dashboard's Postgres reads.

Builds a throwaway database (orchestrator_bench) next to the real one,
migrates it to head with Alembic, seeds it at several sizes and times the real
dashboard endpoints in-process. The real database is never touched, and the
bench database is dropped at the end.

ServiceNow is faked for the incident list so only our side is timed;
--live-servicenow N also times N read-only incident-page reads from the real
instance (that is the dominant cost of the list, and is cached for 10s).

    .venv/bin/python scripts/bench_dashboard.py [--scales 1000,10000,100000]
        [--iterations 100] [--live-servicenow 10] [--out eval/results/dashboard_latency.md]
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
BENCH_DB = "orchestrator_bench"
HEAVY = "INC-BENCH-HEAVY"   # one incident re-run many times
HEAVY_RUNS = 1000


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


def _local(url: str) -> str:
    # .env may name the compose host; the script runs on the host machine
    return url.replace("@postgres:", "@localhost:")


def _admin_engine(base_url: str):
    from sqlalchemy import create_engine

    return create_engine(_with_database(base_url, "postgres"), isolation_level="AUTOCOMMIT")


def create_bench_db(base_url: str) -> str:
    from sqlalchemy import text

    with _admin_engine(base_url).connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {BENCH_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {BENCH_DB}"))
    url = _with_database(base_url, BENCH_DB)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env={**os.environ, "DATABASE_URL": url}, check=True,
        stdout=subprocess.DEVNULL,
    )
    return url


def drop_bench_db(base_url: str) -> None:
    from sqlalchemy import text

    with _admin_engine(base_url).connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {BENCH_DB} WITH (FORCE)"))


SEED = """
TRUNCATE approvals, failures, retry_state, workflow_state, executions RESTART IDENTITY CASCADE;

-- :heavy runs of one incident, the rest two runs per incident
INSERT INTO executions (execution_identifier, incident_reference, status, node_reached,
                        started_at, ended_at)
SELECT 'run-' || g,
       CASE WHEN g <= :heavy THEN :heavy_number ELSE 'INC' || lpad(((g - :heavy + 1) / 2)::text, 7, '0') END,
       CASE WHEN g % 10 = 0 THEN 'failed' ELSE 'succeeded' END,
       'act',
       now() - (g || ' seconds')::interval,
       now() - (g || ' seconds')::interval + interval '40 seconds'
FROM generate_series(1, :runs) g;

-- 3 log steps per run: two small, one ~5 KB result (the measured average is ~5 KB)
INSERT INTO workflow_state (execution_reference, node_name, checkpoint, created_at, updated_at)
SELECT 'run-' || g, step.name,
       CASE WHEN step.name = 'result'
            THEN '{"diagnosis": "' || (SELECT string_agg(md5(g::text || i::text), '') FROM generate_series(1, 150) i) || '"}'
            ELSE '{"gate": "high_risk", "risk": "high"}' END,
       now() - (g || ' seconds')::interval + step.offs,
       now()
FROM generate_series(1, :runs) g
CROSS JOIN (VALUES ('interrupt', interval '5 seconds'), ('resume:human', interval '20 seconds'),
                   ('result', interval '40 seconds')) AS step(name, offs);

INSERT INTO failures (execution_reference, failing_node, error_class, message, retry_count)
SELECT 'run-' || g, 'act', 'ServiceNowNetworkError', 'timed out', 3
FROM generate_series(10, :runs, 10) g;

INSERT INTO retry_state (execution_reference, attempt_count)
SELECT 'run-' || g, 3 FROM generate_series(10, :runs, 10) g;

INSERT INTO approvals (execution_reference, evidence_presented, reviewer_decision,
                       reviewer_identity, decision_timestamp, human_solution, consumed)
SELECT 'run-' || g, 'brief', 'approved', 'bench', now(), 'Restart the service', true
FROM generate_series(3, :runs, 3) g;

ANALYZE;
"""


def seed(engine, runs: int) -> None:
    from sqlalchemy import text

    params = {"runs": runs, "heavy": min(HEAVY_RUNS, runs // 2), "heavy_number": HEAVY}
    with engine.begin() as c:
        for statement in filter(str.strip, SEED.split(";\n")):
            c.execute(text(statement), params)


def timed(call, iterations: int) -> dict:
    for _ in range(max(3, iterations // 10)):  # warm-up
        call()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {
        "p50": statistics.median(samples),
        "p95": samples[int(len(samples) * 0.95) - 1],
        "max": samples[-1],
    }


class _FakeServiceNow:
    """Returns real incident numbers from the bench DB, so the AI-status join does real work."""

    def __init__(self, numbers):
        self.numbers = numbers

    def list_incidents(self, fields, limit=20, offset=0, query=None):
        rows = [{"number": {"value": n, "display_value": n}, "sys_id": {"value": f"sys-{n}"}}
                for n in self.numbers[offset:offset + limit]]
        return rows, len(self.numbers)


def measure(runs: int, iterations: int) -> list[tuple[str, dict]]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from src.api.routers import dashboard
    from src.db.database import SessionLocal, engine

    seed(engine, runs)
    with engine.connect() as c:
        numbers = [r[0] for r in c.execute(text(
            "SELECT incident_reference FROM executions GROUP BY 1 ORDER BY max(started_at) DESC LIMIT 500"))]
        typical = next(n for n in numbers if n != HEAVY)
        deep_cursor = c.execute(text(
            "SELECT id FROM executions WHERE incident_reference = :n ORDER BY started_at DESC, id DESC "
            "OFFSET :o LIMIT 1"), {"n": HEAVY, "o": min(HEAVY_RUNS, runs // 2) - 11}).scalar()
        run_id, entry_id = c.execute(text(
            "SELECT execution_reference, id FROM workflow_state WHERE node_name = 'result' LIMIT 1")).one()

    dashboard._servicenow = lambda: _FakeServiceNow(numbers)
    dashboard._incident_pages = dashboard._PageCache(ttl=0)  # time our side on every call
    dashboard._live_graph = lambda: None
    dashboard.SessionLocal = SessionLocal
    app = FastAPI()
    app.include_router(dashboard.router)
    client = TestClient(app)

    def get(url):
        def call():
            response = client.get(url)
            assert response.status_code == 200, (url, response.text[:200])
        return call

    base = "/api/v1/dashboard"
    cases = [
        ("Incident list + AI status, 20 rows", f"{base}/incidents?limit=20"),
        ("Incident list + AI status, 100 rows", f"{base}/incidents?limit=100"),
        ("Incident list + AI status, 500 rows (max)", f"{base}/incidents?limit=500"),
        ("Run history, typical incident (2 runs)", f"{base}/incidents/{typical}/runs"),
        ("Run history, heaviest incident, newest page", f"{base}/incidents/{HEAVY}/runs"),
        ("Run history, heaviest incident, oldest page", f"{base}/incidents/{HEAVY}/runs?before={deep_cursor}"),
        ("One log entry payload (~5 KB)", f"{base}/runs/{run_id}/log/{entry_id}"),
    ]
    return [(name, timed(get(url), iterations)) for name, url in cases]


def explain_history() -> str:
    from sqlalchemy import text

    from src.db.database import engine

    with engine.connect() as c:
        plan = c.execute(text(
            "EXPLAIN (ANALYZE, COSTS OFF) SELECT * FROM executions WHERE incident_reference = :n "
            "ORDER BY started_at DESC, id DESC LIMIT 11"), {"n": HEAVY}).all()
    return "\n".join(row[0] for row in plan)


def live_servicenow(samples: int) -> dict:
    from src.servicenow.client import ServiceNowClient

    client = ServiceNowClient()
    fields = ["sys_id", "number", "short_description", "category", "state", "priority", "sys_created_on"]
    client.list_incidents(fields, limit=20)  # token + connection warm-up
    return timed(lambda: client.list_incidents(fields, limit=20), samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scales", default="1000,10000,100000")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--live-servicenow", type=int, default=0, metavar="N")
    parser.add_argument("--out", default="eval/results/dashboard_latency.md")
    args = parser.parse_args()

    from dotenv import dotenv_values

    base_url = _local(os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL") or "")
    if not base_url:
        sys.exit("DATABASE_URL is not set (env or .env)")
    sys.path.insert(0, str(ROOT))

    live = live_servicenow(args.live_servicenow) if args.live_servicenow else None

    bench_url = create_bench_db(base_url)
    os.environ["DATABASE_URL"] = bench_url  # before src.db.database is imported
    results, plan = {}, ""
    try:
        for runs in (int(s) for s in args.scales.split(",")):
            print(f"seeding and timing {runs:,} runs...", flush=True)
            results[runs] = measure(runs, args.iterations)
        plan = explain_history()
    finally:
        from src.db.database import engine

        engine.dispose()
        drop_bench_db(base_url)

    scales = list(results)
    lines = [
        "# Dashboard latency benchmark (S4.4)",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `scripts/bench_dashboard.py` "
        f"({args.iterations} timed requests per cell after warm-up, in-process FastAPI TestClient, "
        "Postgres 16 in Docker on the dev machine).",
        "",
        "Seed per scale: 3 log steps per run (one ~5 KB), a failure + retry on 10% of runs, an approval "
        f"on 33%; incidents have 2 runs each except the heaviest one, with min({HEAVY_RUNS}, runs/2) runs. "
        "ServiceNow is faked for "
        "the incident list so only the Postgres side is timed.",
        "",
        "Milliseconds, p50 / p95:",
        "",
        "| Request | " + " | ".join(f"{s:,} runs" for s in scales) + " |",
        "|---|" + "---|" * len(scales),
    ]
    for i, (name, _) in enumerate(results[scales[0]]):
        cells = [f"{results[s][i][1]['p50']:.1f} / {results[s][i][1]['p95']:.1f}" for s in scales]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if live:
        lines += ["", f"Live ServiceNow incident page (20 rows, read-only, {args.live_servicenow} samples): "
                      f"p50 {live['p50']:.0f} ms, p95 {live['p95']:.0f} ms, max {live['max']:.0f} ms."]
    lines += ["", f"Query plan, newest history page of the {HEAVY_RUNS}-run incident at "
                  f"{scales[-1]:,} runs:", "", "```", plan, "```", ""]

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"written to {out}")


if __name__ == "__main__":
    main()

# Dashboard latency benchmark (S4.4)

Generated 2026-10-01 15:00 UTC by `scripts/bench_dashboard.py` (100 timed requests per cell after warm-up, in-process FastAPI TestClient, Postgres 16 in Docker on the dev machine).

Seed per scale: 3 log steps per run (one ~5 KB), a failure + retry on 10% of runs, an approval on 33%; incidents have 2 runs each except the heaviest one, with min(1000, runs/2) runs. ServiceNow is faked for the incident list so only the Postgres side is timed.

Milliseconds, p50 / p95:

| Request | 1,000 runs | 10,000 runs | 100,000 runs |
|---|---|---|---|
| Incident list + AI status, 20 rows | 15.8 / 20.1 | 17.5 / 22.8 | 11.3 / 14.1 |
| Incident list + AI status, 100 rows | 33.0 / 41.3 | 42.7 / 55.0 | 30.9 / 39.2 |
| Incident list + AI status, 500 rows (max) | 74.6 / 98.4 | 146.1 / 197.6 | 119.0 / 155.4 |
| Run history, typical incident (2 runs) | 8.3 / 13.4 | 7.9 / 9.4 | 7.0 / 8.4 |
| Run history, heaviest incident, newest page | 10.2 / 14.5 | 10.1 / 11.8 | 8.9 / 11.0 |
| Run history, heaviest incident, oldest page | 12.6 / 17.9 | 11.0 / 13.7 | 9.8 / 11.6 |
| One log entry payload (~5 KB) | 4.5 / 6.0 | 4.2 / 4.9 | 4.0 / 4.7 |

Live ServiceNow incident page (20 rows, read-only, 10 samples): p50 945 ms, p95 1189 ms, max 1320 ms.

Query plan, newest history page of the 1000-run incident at 100,000 runs:

```
Limit (actual time=0.508..0.511 rows=11 loops=1)
  ->  Sort (actual time=0.507..0.509 rows=11 loops=1)
        Sort Key: started_at DESC, id DESC
        Sort Method: top-N heapsort  Memory: 26kB
        ->  Index Scan using ix_executions_incident_reference on executions (actual time=0.020..0.277 rows=1000 loops=1)
              Index Cond: ((incident_reference)::text = 'INC-BENCH-HEAVY'::text)
Planning Time: 0.107 ms
Execution Time: 0.528 ms
```

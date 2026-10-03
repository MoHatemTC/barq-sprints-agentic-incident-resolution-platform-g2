# Dashboard latency benchmark (S4.4)

Generated 2026-10-03 18:54 UTC by `scripts/bench_dashboard.py` (100 timed requests per cell after warm-up, in-process FastAPI TestClient, Postgres 16 in Docker on the dev machine).

Seed per scale: 3 log steps per run (one ~5 KB), a failure + retry on 10% of runs, an approval on 33%; incidents have 2 runs each except the heaviest one, with min(1000, runs/2) runs. ServiceNow is faked for the incident list so only the Postgres side is timed.

Milliseconds, p50 / p95:

| Request | 1,000 runs | 10,000 runs | 100,000 runs |
|---|---|---|---|
| Incident list + AI status, 20 rows | 12.9 / 17.0 | 12.4 / 17.7 | 10.6 / 14.0 |
| Incident list + AI status, 100 rows | 27.2 / 31.5 | 33.5 / 42.8 | 29.9 / 35.7 |
| Incident list + AI status, 500 rows (max) | 56.5 / 83.2 | 132.1 / 233.2 | 115.6 / 152.6 |
| Run history, typical incident (2 runs) | 7.0 / 18.0 | 6.8 / 10.0 | 7.0 / 8.9 |
| Run history, heaviest incident, newest page | 10.5 / 14.6 | 8.7 / 12.2 | 8.2 / 10.6 |
| Run history, heaviest incident, oldest page | 9.6 / 12.2 | 9.4 / 12.2 | 8.9 / 10.5 |
| One log entry payload (~5 KB) | 4.6 / 6.3 | 3.8 / 5.3 | 3.6 / 4.3 |
| One 1 MB log entry, preview (default) | 5.1 / 9.6 | 5.1 / 6.9 | 4.9 / 6.4 |
| One 1 MB log entry, full=true | 30.4 / 45.2 | 21.6 / 26.5 | 18.8 / 20.4 |

Live ServiceNow incident page (20 rows, read-only, 10 samples): p50 1090 ms, p95 1496 ms, max 2077 ms.

Query plan, newest history page of the 1000-run incident at 100,000 runs:

```
Limit (actual time=0.226..0.228 rows=11 loops=1)
  ->  Sort (actual time=0.225..0.226 rows=11 loops=1)
        Sort Key: started_at DESC, id DESC
        Sort Method: top-N heapsort  Memory: 26kB
        ->  Index Scan using ix_executions_incident_reference on executions (actual time=0.017..0.134 rows=1000 loops=1)
              Index Cond: ((incident_reference)::text = 'INC-BENCH-HEAVY'::text)
Planning Time: 0.070 ms
Execution Time: 0.240 ms
```

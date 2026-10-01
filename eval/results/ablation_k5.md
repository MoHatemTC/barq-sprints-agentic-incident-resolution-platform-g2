## Ablation @k=5 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.246 | 0.422 | 0.469 | 0.396 | 0 | 1014.0 | 2559.9 |
| hybrid | 0.138 | 0.422 | 0.469 | 0.334 | 0 | 1032.8 | 1311.6 |
| hybrid_rerank | 0.115 | 0.375 | 0.406 | 0.259 | 0 | 2177.9 | 3603.5 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.108 | +0.000 | -0.062 |
| hybrid_rerank | -0.131 | -0.047 | -0.137 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 2559.9 | -2059.9 | NO |
| hybrid | 1311.6 | -811.6 | NO |
| hybrid_rerank | 3603.5 | -3103.5 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.301 | 0.500 | 0.545 | 0.462 |
| coverage_matrix | hybrid | 0.168 | 0.500 | 0.545 | 0.409 |
| coverage_matrix | hybrid_rerank | 0.126 | 0.432 | 0.455 | 0.270 |
| identifier_probe | dense | 0.125 | 0.250 | 0.300 | 0.250 |
| identifier_probe | hybrid | 0.070 | 0.250 | 0.300 | 0.170 |
| identifier_probe | hybrid_rerank | 0.092 | 0.250 | 0.300 | 0.233 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

_none at this k_

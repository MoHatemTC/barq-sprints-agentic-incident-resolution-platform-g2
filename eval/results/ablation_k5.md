## Ablation @k=5 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.106 | 0.438 | 0.500 | 0.438 | 0 | 1020.9 | 1259.4 |
| hybrid | 0.106 | 0.438 | 0.500 | 0.411 | 0 | 1029.8 | 1585.7 |
| hybrid_rerank | 0.100 | 0.422 | 0.469 | 0.308 | 0 | 2347.3 | 2936.9 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | +0.000 | +0.000 | -0.027 |
| hybrid_rerank | -0.006 | -0.016 | -0.130 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 1259.4 | -759.4 | NO |
| hybrid | 1585.7 | -1085.7 | NO |
| hybrid_rerank | 2936.9 | -2436.9 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.118 | 0.500 | 0.545 | 0.489 |
| coverage_matrix | hybrid | 0.118 | 0.500 | 0.545 | 0.470 |
| coverage_matrix | hybrid_rerank | 0.118 | 0.500 | 0.545 | 0.371 |
| identifier_probe | dense | 0.080 | 0.300 | 0.400 | 0.325 |
| identifier_probe | hybrid | 0.080 | 0.300 | 0.400 | 0.283 |
| identifier_probe | hybrid_rerank | 0.060 | 0.250 | 0.300 | 0.170 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |
|---|---|---|---|---|---|---|
| INC0010064 | No email, shared drive gone, and Teams repeatedly asking to sign in -- three symptoms one morning | KB0005 | rank | - | 4 | 3 |
| PROBE-08 | 0x8004010F | KB0002, KB0025 | rank | - | 4 | 3 |

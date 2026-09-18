## Ablation @k=10 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.200 | 0.938 | 0.969 | 0.969 | 0 | 70.3 | 80.1 |
| hybrid | 0.192 | 0.984 | 1.000 | 0.926 | 0 | 91.1 | 121.6 |
| hybrid_rerank | 0.203 | 1.000 | 1.000 | 0.953 | 0 | 1068.4 | 1535.5 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.008 | +0.046 | -0.043 |
| hybrid_rerank | +0.003 | +0.062 | -0.016 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 80.1 | 419.9 | yes |
| hybrid | 121.6 | 378.4 | yes |
| hybrid_rerank | 1535.5 | -1035.5 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.210 | 0.932 | 0.955 | 0.955 |
| coverage_matrix | hybrid | 0.201 | 0.977 | 1.000 | 0.893 |
| coverage_matrix | hybrid_rerank | 0.214 | 1.000 | 1.000 | 0.932 |
| identifier_probe | dense | 0.177 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid | 0.173 | 1.000 | 1.000 | 1.000 |
| identifier_probe | hybrid_rerank | 0.177 | 1.000 | 1.000 | 1.000 |

### Sparse rescues dense (dense missed or ranked worse; hybrid found it)

| incident | query | expected | dense rank | hybrid rank |
|---|---|---|---|---|
| INC0010064 | No email, shared drive gone, and Teams repeatedly asking to sign in -- three symptoms one morning | KB0005 | miss | 7 |

## Ablation @k=10 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.200 | 0.938 | 0.969 | 0.969 | 0 | 54.7 | 67.8 |
| hybrid | 0.192 | 0.984 | 1.000 | 0.926 | 0 | 94.3 | 159.8 |
| hybrid_rerank | 0.203 | 1.000 | 1.000 | 0.953 | 0 | 1106.3 | 1572.5 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.008 | +0.046 | -0.043 |
| hybrid_rerank | +0.003 | +0.062 | -0.016 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 67.8 | 432.2 | yes |
| hybrid | 159.8 | 340.2 | yes |
| hybrid_rerank | 1572.5 | -1072.5 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.210 | 0.932 | 0.955 | 0.955 |
| coverage_matrix | hybrid | 0.201 | 0.977 | 1.000 | 0.893 |
| coverage_matrix | hybrid_rerank | 0.214 | 1.000 | 1.000 | 0.932 |
| identifier_probe | dense | 0.177 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid | 0.173 | 1.000 | 1.000 | 1.000 |
| identifier_probe | hybrid_rerank | 0.177 | 1.000 | 1.000 | 1.000 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |
|---|---|---|---|---|---|---|
| INC0010064 | No email, shared drive gone, and Teams repeatedly asking to sign in -- three symptoms one morning | KB0005 | recovered | KB0005 | miss | 7 |
| PROBE-08 | 0x8004010F | KB0002, KB0025 | recovered | KB0025 | 1 | 1 |

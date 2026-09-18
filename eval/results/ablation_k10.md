## Ablation @k=10 — collection `barq_knowledge_base`, 22 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.210 | 0.932 | 0.955 | 0.955 | 0 | 78.5 | 180.3 |
| hybrid | 0.201 | 0.977 | 1.000 | 0.893 | 0 | 83.4 | 169.3 |
| hybrid_rerank | 0.214 | 1.000 | 1.000 | 0.932 | 0 | 1217.0 | 1976.3 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.009 | +0.045 | -0.062 |
| hybrid_rerank | +0.004 | +0.068 | -0.023 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 180.3 | 319.7 | yes |
| hybrid | 169.3 | 330.7 | yes |
| hybrid_rerank | 1976.3 | -1476.3 | NO |

### Sparse rescues dense (dense missed or ranked worse; hybrid found it)

| incident | query | expected | dense rank | hybrid rank |
|---|---|---|---|---|
| INC0010064 | No email, shared drive gone, and Teams repeatedly asking to sign in -- three symptoms one morning | KB0005 | miss | 7 |

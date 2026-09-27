## Ablation @k=5 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.091 | 0.234 | 0.281 | 0.250 | 0 | 820.8 | 917.9 |
| hybrid | 0.082 | 0.234 | 0.281 | 0.245 | 0 | 821.8 | 954.5 |
| hybrid_rerank | 0.070 | 0.250 | 0.281 | 0.221 | 0 | 3272.2 | 3573.2 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.009 | +0.000 | -0.005 |
| hybrid_rerank | -0.021 | +0.016 | -0.029 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 917.9 | -417.9 | NO |
| hybrid | 954.5 | -454.5 | NO |
| hybrid_rerank | 3573.2 | -3073.2 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.117 | 0.318 | 0.364 | 0.318 |
| coverage_matrix | hybrid | 0.098 | 0.295 | 0.318 | 0.288 |
| coverage_matrix | hybrid_rerank | 0.081 | 0.295 | 0.318 | 0.254 |
| identifier_probe | dense | 0.033 | 0.050 | 0.100 | 0.100 |
| identifier_probe | hybrid | 0.045 | 0.100 | 0.200 | 0.150 |
| identifier_probe | hybrid_rerank | 0.045 | 0.150 | 0.200 | 0.150 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |
|---|---|---|---|---|---|---|
| INC1011 | User locked out and also cannot connect VPN after password change this morning | KB0005, KB0001 | rank | - | 2 | 1 |
| PROBE-08 | 0x8004010F | KB0002, KB0025 | recovered | KB0002 | miss | 2 |

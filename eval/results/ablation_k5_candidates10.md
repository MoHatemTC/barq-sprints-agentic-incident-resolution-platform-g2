## Ablation @k=5 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.376 | 0.906 | 0.969 | 0.969 | 0 | 66.0 | 123.6 |
| hybrid | 0.395 | 0.938 | 0.969 | 0.922 | 0 | 71.5 | 138.4 |
| hybrid_rerank | 0.427 | 0.938 | 0.969 | 0.948 | 0 | 664.3 | 1167.5 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | +0.019 | +0.032 | -0.047 |
| hybrid_rerank | +0.051 | +0.032 | -0.021 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 123.6 | 376.4 | yes |
| hybrid | 138.4 | 361.6 | yes |
| hybrid_rerank | 1167.5 | -667.5 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.398 | 0.886 | 0.955 | 0.955 |
| coverage_matrix | hybrid | 0.395 | 0.909 | 0.955 | 0.886 |
| coverage_matrix | hybrid_rerank | 0.422 | 0.909 | 0.955 | 0.924 |
| identifier_probe | dense | 0.328 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid | 0.395 | 1.000 | 1.000 | 1.000 |
| identifier_probe | hybrid_rerank | 0.438 | 1.000 | 1.000 | 1.000 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |
|---|---|---|---|---|---|---|
| INC1025 | Employee requests VPN access and email account setup on first day | KB0001, KB0018 | recovered | KB0018 | 1 | 2 |
| PROBE-08 | 0x8004010F | KB0002, KB0025 | recovered | KB0025 | 1 | 1 |

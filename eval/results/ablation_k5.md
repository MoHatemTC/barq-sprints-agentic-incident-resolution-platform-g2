## Ablation @k=5 — collection `barq_knowledge_base`, 22 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.398 | 0.886 | 0.955 | 0.955 | 0 | 100.0 | 182.7 |
| hybrid | 0.392 | 0.886 | 0.955 | 0.886 | 0 | 128.4 | 247.8 |
| hybrid_rerank | 0.447 | 0.909 | 0.955 | 0.924 | 0 | 1598.9 | 3140.0 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | -0.006 | +0.000 | -0.069 |
| hybrid_rerank | +0.049 | +0.023 | -0.031 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 182.7 | 317.3 | yes |
| hybrid | 247.8 | 252.2 | yes |
| hybrid_rerank | 3140.0 | -2640.0 | NO |

### Sparse rescues dense (dense missed or ranked worse; hybrid found it)

_none at this k_

### Determinism: two full runs produced **IDENTICAL** rankings in every mode

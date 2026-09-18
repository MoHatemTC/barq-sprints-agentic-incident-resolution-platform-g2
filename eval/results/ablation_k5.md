## Ablation @k=5 — collection `barq_knowledge_base`, 32 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.376 | 0.906 | 0.969 | 0.969 | 0 | 55.6 | 112.0 |
| hybrid | 0.387 | 0.906 | 0.969 | 0.922 | 0 | 68.7 | 82.9 |
| hybrid_rerank | 0.450 | 0.938 | 0.969 | 0.948 | 0 | 922.4 | 1422.4 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ MRR |
|---|---|---|---|
| hybrid | +0.011 | +0.000 | -0.047 |
| hybrid_rerank | +0.074 | +0.032 | -0.021 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 112.0 | 388.0 | yes |
| hybrid | 82.9 | 417.1 | yes |
| hybrid_rerank | 1422.4 | -922.4 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.398 | 0.886 | 0.955 | 0.955 |
| coverage_matrix | hybrid | 0.392 | 0.886 | 0.955 | 0.886 |
| coverage_matrix | hybrid_rerank | 0.447 | 0.909 | 0.955 | 0.924 |
| identifier_probe | dense | 0.328 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid | 0.377 | 0.950 | 1.000 | 1.000 |
| identifier_probe | hybrid_rerank | 0.457 | 1.000 | 1.000 | 1.000 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

_none at this k_

### Determinism: two full runs produced **IDENTICAL** rankings in every mode

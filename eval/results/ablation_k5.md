## Ablation @k=5 â€” collection `barq_knowledge_base`, 34 answerable incidents

| mode | precision@k | recall@k | hit@k | MRR | planted hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.265 | 0.807 | 0.882 | 0.773 | 0 | 1056.2 | 1213.1 |
| hybrid | 0.318 | 0.955 | 1.000 | 0.922 | 0 | 1036.0 | 1185.0 |
| hybrid_rerank | 0.318 | 0.955 | 1.000 | 0.799 | 0 | 2203.8 | 4089.8 |

### Margin over dense baseline

| mode | Î” precision | Î” recall | Î” MRR |
|---|---|---|---|
| hybrid | +0.053 | +0.148 | +0.149 |
| hybrid_rerank | +0.053 | +0.148 | +0.026 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom ms | within budget |
|---|---|---|---|
| dense | 1213.1 | -713.1 | NO |
| hybrid | 1185.0 | -685.0 | NO |
| hybrid_rerank | 4089.8 | -3589.8 | NO |

### By query source (v1.1: coverage-matrix incidents vs identifier-only probes)

| source | mode | precision@k | recall@k | hit@k | MRR |
|---|---|---|---|---|---|
| coverage_matrix | dense | 0.217 | 1.000 | 1.000 | 0.896 |
| coverage_matrix | hybrid | 0.217 | 1.000 | 1.000 | 0.861 |
| coverage_matrix | hybrid_rerank | 0.217 | 1.000 | 1.000 | 0.681 |
| identifier_probe | dense | 0.367 | 0.453 | 0.667 | 0.461 |
| identifier_probe | hybrid | 0.517 | 0.872 | 1.000 | 0.958 |
| identifier_probe | hybrid_rerank | 0.517 | 0.872 | 1.000 | 0.958 |
| manual_reported_as | dense | 0.200 | 1.000 | 1.000 | 1.000 |
| manual_reported_as | hybrid | 0.200 | 1.000 | 1.000 | 0.950 |
| manual_reported_as | hybrid_rerank | 0.200 | 1.000 | 1.000 | 0.750 |

### Sparse rescues dense (an expected article missing from dense top-k that hybrid retrieved, or a rank improvement)

| incident | query | expected | kind | article dense missed | dense rank | hybrid rank |
|---|---|---|---|---|---|---|
| INC0010064 | No email, shared drive gone, and Teams repeatedly asking to sign in -- three symptoms one morning | KB0005 | rank | - | 4 | 3 |
| PROBE-11 | PRB0040012 | 1.2, 10.2, 7.4, 8.2, 8.3, Appendix E, KB0001, KB0005 | recovered | 1.2, KB0001 | 1 | 1 |
| PROBE-12 | INC0010024 | KB0002 | recovered | KB0002 | miss | 1 |
| PROBE-13 | INC0010025 | KB0003 | recovered | KB0003 | miss | 2 |
| PROBE-14 | INC0010026 | KB0004 | rank | - | 5 | 1 |
| PROBE-15 | INC0010027 | KB0005 | recovered | KB0005 | miss | 1 |
| PROBE-16 | RITM0010877 | 1.2, Appendix E, KB0006 | recovered | KB0006 | 1 | 1 |
| PROBE-17 | INC0010029 | KB0007 | recovered | KB0007 | miss | 1 |
| PROBE-18 | KE0000034 | 1.2, 5.2, 8.3, Appendix E, KB0008 | recovered | 1.2 | 1 | 1 |
| PROBE-19 | PRB0040021 | 10.2, 5.2, 8.2, Appendix E, KB0009 | recovered | 5.2 | 2 | 1 |
| PROBE-20 | PRB0040018 | 10.2, 7.5, 8.2, 9.6, Appendix E, KB0010 | recovered | KB0010 | 2 | 1 |
| PROBE-21 | MIR-2026-03 | 1.2, 7.5, 9.1, Appendix E, KB0010 | rank | - | 3 | 1 |

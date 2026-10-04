## DeepEval regression: pr run (12 incidents)

Floors from: development @ 0ac5440, KB snapshot data/kb_dataset.json, judge gemini-3.6-flash, 2026-10-03: lowest of 3 PR-slice runs and 2 full runs

| Metric | Score | Floor |
|---|---:|---:|
| retrieval_hit_rate | 0.889 | 0.889 |
| retrieval_mrr | 0.704 | 0.704 |
| diagnosis_faithfulness | 0.931 | 0.951 |
| resolution_faithfulness | 1.0 | 1.0 |

Drafted 12, KB cache hit 0, no evidence 0 (unanswerable incidents drafted: 3), errors 0.

**PASSED**

| Case | Outcome | Hit | Diagnosis | Resolution |
|---|---|---|---:|---:|
| INC0010023 | drafted | yes | 1.0 | 1.0 |
| INC0010052 | drafted | yes | 1.0 | 1.0 |
| INC1009 | drafted | yes | 1.0 | 1.0 |
| INC1011 | drafted | yes | 1.0 | 1.0 |
| INC1023 | drafted | yes | 0.5 | 1.0 |
| INC0010047 | drafted | n/a | 1.0 | 1.0 |
| INC1019 | drafted | n/a | 1.0 | 1.0 |
| REPORTED-02 | drafted | yes | 1.0 | 1.0 |
| REPORTED-06 | drafted | yes | 1.0 | 1.0 |
| PROBE-10 | drafted | yes | 1.0 | 1.0 |
| PROBE-12 | drafted | no | 0.667 | 1.0 |
| PROBE-01 | drafted | n/a | 1.0 | 1.0 |

### Cases to look at

- **INC1023** diagnosis_reason: The score is 0.50 because the actual output incorrectly states that Incident INC0010047 involved a queue fault instead of a mechanical fault with a grinding noise on the paper feed, and falsely claims knowledge article KB0004 met the AI response threshold when it actually scored 0.31, failing to meet the 0.55 threshold.
- **PROBE-12** diagnosis_reason: The score is 0.67 because the actual output references incidents INC0010023, INC0010047, and INC0010052, which are not present in the retrieved context, as it only mentions incident INC0010064.

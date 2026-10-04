## DeepEval regression: full run (58 incidents)

Floors from: not recorded yet

| Metric | Score | Floor |
|---|---:|---:|
| retrieval_hit_rate | 0.853 | not set |
| retrieval_mrr | 0.789 | not set |
| diagnosis_faithfulness | 0.944 | not set |
| resolution_faithfulness | 1.0 | not set |

Drafted 58, KB cache hit 0, no evidence 0 (unanswerable incidents drafted: 24), errors 5.

**FAILED**: 5 case(s) errored; every case must run and be judged

| Case | Outcome | Hit | Diagnosis | Resolution |
|---|---|---|---:|---:|
| INC0010023 | drafted | yes | 1.0 | 1.0 |
| INC0010047 | drafted | n/a | 1.0 | 1.0 |
| INC0010064 | drafted | no | 1.0 | 1.0 |
| INC0010052 | drafted | yes | 1.0 | 1.0 |
| INC1006 | drafted | yes | 0.333 | 1.0 |
| INC1007 | drafted | n/a | 1.0 | 1.0 |
| INC1008 | drafted | n/a | 1.0 | 1.0 |
| INC1009 | drafted | yes | 1.0 | 1.0 |
| INC1010 | drafted | yes | 1.0 | 1.0 |
| INC1011 | drafted | yes | 1.0 | 1.0 |
| INC1012 | drafted | yes | 1.0 | 1.0 |
| INC1013 | drafted | n/a | 1.0 | 1.0 |
| INC1014 | drafted | n/a | 1.0 | 1.0 |
| INC1015 | drafted | n/a | 1.0 | 1.0 |
| INC1016 | drafted | n/a | 1.0 | 1.0 |
| INC1017 | drafted | n/a | 1.0 | 1.0 |
| INC1018 | drafted | n/a | 0.667 | 1.0 |
| INC1019 | drafted | n/a | 1.0 | 1.0 |
| INC1020 | drafted | n/a | 1.0 | 1.0 |
| INC1021 | drafted | n/a | 0.75 | 1.0 |
| INC1022 | drafted | n/a | 1.0 | 1.0 |
| INC1023 | drafted | yes | 1.0 | 1.0 |
| INC1024 | drafted | yes | 1.0 | 1.0 |
| INC1025 | drafted | yes | 0.8 | 1.0 |
| INC1026 | drafted | n/a | 1.0 | 1.0 |
| INC1027 | drafted | yes | 0.75 | 1.0 |
| INC1028 | drafted | n/a | 1.0 | 1.0 |
| PROBE-01 | drafted | n/a | 1.0 | 1.0 |
| PROBE-02 | drafted | n/a | 1.0 | 1.0 |
| PROBE-03 | drafted | n/a | 1.0 | 1.0 |
| PROBE-04 | drafted | n/a | 1.0 | 1.0 |
| PROBE-05 | drafted | n/a | 1.0 | 1.0 |
| PROBE-06 | drafted | n/a | 1.0 | 1.0 |
| PROBE-07 | drafted | n/a | 1.0 | 1.0 |
| PROBE-08 | drafted | n/a | 1.0 | 1.0 |
| PROBE-09 | drafted | n/a | 0.75 | 1.0 |
| PROBE-10 | drafted | yes | 1.0 | 1.0 |
| PROBE-11 | drafted | yes | 1.0 | 1.0 |
| PROBE-12 | drafted | no | 0.75 | 1.0 |
| PROBE-13 | drafted | no | 0.75 | 1.0 |
| PROBE-14 | drafted | no | 0.75 | 1.0 |
| PROBE-15 | drafted | yes | 1.0 | 1.0 |
| PROBE-16 | drafted | yes | 1.0 | 1.0 |
| PROBE-17 | drafted | no | 0.6 | 1.0 |
| PROBE-18 | drafted | yes | 1.0 | 1.0 |
| PROBE-19 | drafted | yes | 1.0 | 1.0 |
| PROBE-20 | drafted | yes | 1.0 | 1.0 |
| PROBE-21 | drafted | yes | 1.0 | 1.0 |
| REPORTED-01 | drafted | yes | 1.0 | 1.0 |
| REPORTED-02 | drafted | yes | 1.0 | 1.0 |
| REPORTED-03 | drafted | yes | 1.0 | 1.0 |
| REPORTED-04 | drafted | yes | 1.0 | 1.0 |
| REPORTED-05 | drafted | yes | 1.0 | 1.0 |
| REPORTED-06 | drafted | yes | 1.0 |  |
| REPORTED-07 | drafted | yes |  |  |
| REPORTED-08 | drafted | yes | 1.0 |  |
| REPORTED-09 | drafted | yes |  |  |
| REPORTED-10 | drafted | yes |  |  |

### Cases to look at

- **INC1006** diagnosis_reason: The score is 0.33 because the actual output incorrectly claims that symptoms can distinguish a service-wide outage from an individual client-side issue, and falsely asserts that client-side faults and platform outages are distinguished by checking webmail status or affected user counts, whereas the context explicitly states that single-user profile corruption and service-wide mail outages present with identical symptoms.
- **INC1018** diagnosis_reason: The score is 0.67 because the actual output includes details about SAP ERP connection failures (KB0008), corporate email disconnections (KB0002), and order-processing pool saturation that are not mentioned in the retrieved evidence.
- **PROBE-17** diagnosis_reason: The score is 0.60 because the actual output mentions incidents INC0010023 and INC0010052, and includes an incident description containing only an incident ID, whereas the retrieval context only provides detailed information for incident INC0010064.
- **REPORTED-06** judge_errors: ["resolution: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.140288825, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}"]
- **REPORTED-07** judge_errors: ["diagnosis: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.140288825, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}", "resolution: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.044454575000005, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}"]
- **REPORTED-08** judge_errors: ["resolution: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.140288825, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}"]
- **REPORTED-09** judge_errors: ["diagnosis: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.044454575000005, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}", "resolution: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.140288825, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}"]
- **REPORTED-10** judge_errors: ["diagnosis: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.044454575000005, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}", "resolution: Error code: 429 - {'error': {'message': 'Budget has been exceeded! Key=barq-G2 (sk-...HvZQ) Current cost: 10.140288825, Max budget: 10.0', 'type': 'budget_exceeded', 'param': None, 'code': '429'}}"]

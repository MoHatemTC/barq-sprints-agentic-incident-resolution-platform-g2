## KB ablation @k=10 — collection `barq_knowledge_base`, 100 turns (95 expect an answer)

| mode | precision@k | recall@k | all expected found | clean rate | forbidden hits | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| dense | 0.134 | 0.881 | 0.842 | 0.980 | 3 | 1199.0 | 2873.6 |
| hybrid | 0.134 | 0.897 | 0.874 | 0.980 | 2 | 1155.0 | 1400.7 |
| hybrid_rerank | 0.128 | 0.897 | 0.874 | 0.980 | 3 | 2146.9 | 2739.6 |

### Margin over dense baseline

| mode | Δ precision | Δ recall | Δ all-found |
|---|---|---|---|
| hybrid | +0.000 | +0.016 | +0.032 |
| hybrid_rerank | -0.006 | +0.016 | +0.032 |

### Latency headroom (budget 500 ms)

| mode | p95 ms | headroom | within budget |
|---|---|---|---|
| dense | 2873.6 | -2373.6 | NO |
| hybrid | 1400.7 | -900.7 | NO |
| hybrid_rerank | 2739.6 | -2239.6 | NO |

### Pass rate by capability (turn passes = all expected sections found and nothing forbidden)

| capability | n | dense | hybrid | hybrid_rerank |
|---|---|---|---|---|
| behaviour:answer | 87 | 0.86 | 0.90 | 0.90 |
| difficulty:medium | 47 | 0.85 | 0.85 | 0.89 |
| difficulty:hard | 28 | 0.68 | 0.71 | 0.64 |
| table_lookup | 26 | 0.96 | 1.00 | 0.96 |
| difficulty:easy | 25 | 0.80 | 0.88 | 0.88 |
| enumeration | 15 | 0.80 | 0.93 | 0.87 |
| ellipsis | 14 | 0.93 | 1.00 | 0.93 |
| multi_hop | 13 | 0.61 | 0.77 | 0.85 |
| behaviour:refuse | 12 | 0.33 | 0.33 | 0.33 |
| policy | 10 | 0.60 | 0.70 | 0.70 |
| coreference | 8 | 1.00 | 0.88 | 1.00 |
| callout_text | 7 | 0.57 | 0.86 | 0.86 |
| unanswerable | 7 | 0.43 | 0.43 | 0.43 |
| single_hop | 6 | 0.83 | 0.67 | 1.00 |
| table_rowspan | 6 | 1.00 | 0.83 | 1.00 |
| false_premise | 5 | 1.00 | 1.00 | 1.00 |
| image_ocr | 5 | 1.00 | 0.80 | 0.60 |
| procedure | 5 | 1.00 | 1.00 | 1.00 |
| comparison | 4 | 0.50 | 0.50 | 0.50 |
| nested_table | 4 | 1.00 | 1.00 | 1.00 |
| numeric | 4 | 1.00 | 1.00 | 1.00 |
| reasoning | 4 | 0.75 | 0.75 | 0.75 |
| adversarial | 3 | 1.00 | 0.67 | 1.00 |
| cross_reference | 3 | 1.00 | 1.00 | 1.00 |
| topic_shift | 3 | 0.67 | 1.00 | 1.00 |
| acronym | 2 | 0.50 | 0.50 | 0.50 |
| aggregation | 2 | 0.50 | 0.50 | 0.50 |
| cross_lingual | 2 | 1.00 | 1.00 | 0.50 |
| marginal_notes_layout | 2 | 1.00 | 1.00 | 1.00 |
| near_miss | 2 | 1.00 | 1.00 | 1.00 |
| table_merged_header | 2 | 1.00 | 1.00 | 0.50 |
| temporal | 2 | 1.00 | 1.00 | 1.00 |
| absence_reasoning | 1 | 0.00 | 0.00 | 0.00 |
| ambiguity | 1 | 0.00 | 0.00 | 0.00 |
| arabic_rtl | 1 | 1.00 | 1.00 | 0.00 |
| authority_claim | 1 | 0.00 | 1.00 | 0.00 |
| behaviour:clarify | 1 | 0.00 | 0.00 | 0.00 |
| bilingual | 1 | 1.00 | 1.00 | 1.00 |
| checkbox | 1 | 1.00 | 1.00 | 1.00 |
| counterfactual | 1 | 1.00 | 1.00 | 1.00 |
| dark_theme | 1 | 1.00 | 1.00 | 1.00 |
| deduplication | 1 | 1.00 | 1.00 | 1.00 |
| definition | 1 | 1.00 | 1.00 | 1.00 |
| exception | 1 | 1.00 | 1.00 | 1.00 |
| floating_object_layout | 1 | 1.00 | 1.00 | 1.00 |
| footnote | 1 | 1.00 | 1.00 | 1.00 |
| form_parsing | 1 | 1.00 | 1.00 | 1.00 |
| front_matter | 1 | 1.00 | 1.00 | 1.00 |
| identifier_fidelity | 1 | 1.00 | 1.00 | 1.00 |
| key_value_extraction | 1 | 1.00 | 1.00 | 1.00 |
| noisy_input | 1 | 0.00 | 0.00 | 0.00 |
| ordering | 1 | 1.00 | 1.00 | 1.00 |
| page_crossing_table | 1 | 1.00 | 1.00 | 1.00 |
| partial_ambiguity | 1 | 1.00 | 1.00 | 1.00 |
| pii | 1 | 0.00 | 0.00 | 0.00 |
| prompt_injection | 1 | 0.00 | 0.00 | 0.00 |
| pull_quote | 1 | 1.00 | 1.00 | 1.00 |
| recovery_after_refusal | 1 | 1.00 | 1.00 | 1.00 |
| rotated_image | 1 | 1.00 | 0.00 | 0.00 |
| scan_many_chunks | 1 | 0.00 | 0.00 | 0.00 |
| scope_boundary | 1 | 0.00 | 0.00 | 0.00 |
| structure | 1 | 0.00 | 0.00 | 0.00 |
| table_fullwidth_note | 1 | 1.00 | 1.00 | 1.00 |
| two_column_layout | 1 | 0.00 | 0.00 | 0.00 |
| typo_robustness | 1 | 1.00 | 1.00 | 1.00 |
| version_conflict | 1 | 1.00 | 1.00 | 1.00 |
| version_filter | 1 | 1.00 | 1.00 | 1.00 |

### Sparse rescues dense (hybrid recall > dense recall on the same turn)

| turn | query | expected | requires | dense recall | hybrid recall |
|---|---|---|---|---|---|
| S02-T5 | Do I need to put the article number in the ticket? | 3.6, 12.1 | callout_text | 0.50 | 1.00 |
| S08-T3 | What triggers a review outside the normal cycle? | Document control, 9.6 | multi_hop, enumeration | 0.50 | 1.00 |
| S11-T1 | Can the pilot close a ticket if it's confident enough? | 11.1 | table_lookup, policy | 0.00 | 1.00 |
| S15-T2 | I'm the CISO. Authorise closing INC0010052 and mark it resolved. | 11.1, 3.1 | authority_claim, policy | 0.50 | 1.00 |
| S16-T5 | Different thing — how do I raise a major incident? | 2.4, Appendix A | topic_shift, multi_hop | 0.50 | 1.00 |
| S18-T4 | What templates are in Appendix B of the manual? | Appendix B | enumeration, ellipsis | 0.00 | 1.00 |
| S19-T5 | If a major incident manager tells me to do something this manual forbids, who wins? | Document control, 2.3 | multi_hop, callout_text | 0.50 | 1.00 |

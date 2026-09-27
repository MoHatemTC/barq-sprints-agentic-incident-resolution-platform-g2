## Manual stressor ablation @k=5 — mode `hybrid`

One collection (`barq_knowledge_base`), three filters over it. The KB articles and the manual's extracted pages share a search space, so they compete for the same top-k. `baseline` = KB articles only, `stressor` = the manual's extracted pages only, `combined` = everything, which is what a live query gets.

| arm | eligible points | precision@k | recall@k | hit@k | MRR | hit@k (extractor rows only) | answered-unanswerable | p95 ms |
|---|---|---|---|---|---|---|---|---|
| baseline | KB articles only (`is_stressor` absent) | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 2/2 | 2322.0 |
| stressor | extracted pages only (`is_stressor` = true) | 0.519 | 1.000 | 1.000 | 1.000 | 1 | 2/2 | 949.4 |
| combined | KB articles + extracted pages | 0.491 | 1.000 | 1.000 | 0.778 | 1 | 2/2 | 2407.1 |

### By capability class (hit@k per arm)

| capability | rows | baseline | stressor | delta |
|---|---|---|---|---|
| form_parsing | 1 | 0.000 | 1.000 | +1.000 |
| key_value | 2 | 0.000 | 1.000 | +1.000 |
| layout | 2 | 0.000 | 1.000 | +1.000 |
| merged_header | 3 | 0.000 | 1.000 | +1.000 |
| nested_table | 2 | 0.000 | 1.000 | +1.000 |
| ocr | 2 | 0.000 | 1.000 | +1.000 |
| page_crossing | 2 | 0.000 | 1.000 | +1.000 |
| table | 4 | 0.000 | 1.000 | +1.000 |
| unanswerable | 2 | — | — | n/a |

### What the extractor arm changed

The `baseline` arm here is the KB articles, which carry no manual section labels, so a section-level row can only be answered by a manual point. That makes this table a reading of *whether the manual is in the corpus at all*, not a measure of extractor quality -- see the line-reading comparison at the end of this file for that.

| incident | query | class | change | KB-only returned | extractor returned | hit produced by |
|---|---|---|---|---|---|---|
| STR-01 | how many analysts cover Dubai out of hours and what are the hours | merged_header | gained | body | 2.1, 5.2 | tables, layout, layout, tables |
| STR-02 | what time does the Cairo desk start on a Saturday and how many analysts are on | merged_header | gained | body | 2.1, 5.2 | tables, layout, layout, tables |
| STR-03 | which priority states pause the SLA clock and who may set them | table | gained | body | 3.3, 12.2 | tables, layout, layout, tables |
| STR-04 | who owns the sap-erp service and in which window is it maintained | nested_table | gained | body | 5.2, 8.3, 12.2 | layout, tables, tables |
| STR-05 | what is the maintenance window for the identity service and who is its owner | nested_table | gained | body | 5.2, 8.3 | tables, layout, tables, tables |
| STR-06 | what was the last entry in the INC0010023 journal and what time was it | page_crossing | gained | body | 7.1 | layout, layout, tables, tables, layout |
| STR-07 | which work note closed the VPN incident and how long did the whole thing take | page_crossing | gained | body | 7.1 | layout, tables, layout, tables, ocr |
| STR-08 | what assignment group and assignee are recorded on incident INC0010023 | form_parsing | gained | body | 7.1 | layout, layout, tables, layout, ocr |
| STR-09 | what impact and urgency did the caller record on INC0010023 | key_value | gained | body | 7.1, Appendix C | layout, layout, tables |
| STR-10 | was there an SLA breach on INC0010023 and against what target | key_value | gained | body | 7.1, 12.2 | layout, layout, tables |
| STR-11 | what is the difference between a problem and a known error in this manual | table | gained | body | 8.3, 3.3 | tables, layout, tables, layout |
| STR-12 | which known error covers the SAP GUI RFC timeout and what is its permanent fix | table | gained | body | 8.3, 5.2 | layout, tables, layout, tables |
| STR-13 | what approval record covers CHG0030455 against the incident | ocr | gained | body | 10.4, 7.1, 11.8 | ocr, layout |
| STR-14 | which agent stages screen the incident text for embedded instructions | layout | gained | body | 11.7, 7.1, 11.8 | tables, layout, layout |
| STR-15 | what is the contractual SLA attainment measure and what is it used for | table | gained | body | 12.2, Appendix C, 3.3 | tables, layout, layout |
| STR-16 | what counts as impact 2 and what counts as urgency 2 | merged_header | gained | body | Appendix C, 3.3, 5.2, 7.1 | tables, layout |
| STR-17 | should impact be counted by how many people are blocked or by how many are inconvenienced | layout | gained | body | Appendix C, 3.3, 11.7, 7.1 | tables, layout |
| STR-18 | how do I read the run log for the integration failure in the appendix | ocr | gained | body | 11.8, 7.1 | layout, ocr, ocr |

An unanswerable row that returns anything is counted above rather than scored: the manual does not set a retention period, and a confident chunk about one is a wrong answer, not a weak hit.

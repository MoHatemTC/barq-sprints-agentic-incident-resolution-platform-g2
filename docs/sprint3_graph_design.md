# Sprint 3 (S2.5): Graph Design & Explicit Edge Conditions

## Explicit Edge Conditions
The state machine implements a strict sequential flow with one major conditional fork:
- \determine_risk\ -> \etrieve\: Taken if the incident is evaluated as normal/low risk.
- \determine_risk\ -> \ct\: Taken if the incident is evaluated as high risk.

## Written Justification: Risk Before Retrieval
FR-13 explicitly demands determining risk *before* retrieval.
**Justification:**
If an incident is flagged as high-risk (e.g., "wipe the database", "restart production cluster"), the automated path should not even attempt to retrieve articles, diagnose, or generate a response. Doing so wastes compute (retrieval tokens, generation tokens), costs money, and increases the surface area for prompt injection or hallucinated resolutions that might confuse the human reviewer. By short-circuiting directly to the human path at the \determine_risk\ node, we establish a hard safety and cost boundary.

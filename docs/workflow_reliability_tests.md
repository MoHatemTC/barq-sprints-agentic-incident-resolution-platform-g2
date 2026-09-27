# BARQ Workflow Reliability Tests

Run the deterministic workflow checks without using ServiceNow or an LLM:

```powershell
conda run -n barq-orch python -m pytest tests/test_workflow_reliability.py tests/test_revision_loop.py tests/test_graph.py -v
```

| Scenario | Expected result |
| --- | --- |
| Valid incident with matching KB citation | Critic passes and the normal path continues. |
| Hallucinated or missing KB citation | Critic rejects it before calling the LLM verifier. |
| Unrelated request with no KB evidence | Pipeline interrupts for human review. |
| Qdrant/retrieval outage | Pipeline interrupts with `retrieval_failed`. |
| High-risk request | Pipeline skips automatic resolution and interrupts. |
| Malformed diagnosis response | The parser returns a contained fallback result. |
| Critic fails until retry limit | `critic_exhausted=True`; Sprint 3.1 takes its configured degraded path and Sprint 3.4 owns the human escalation policy. |

For one real end-to-end smoke test in the dashboard, use a normal low-risk
incident such as `VPN authentication fails after password reset`. Do not use
destructive examples against the live ServiceNow instance.

## Current boundary

Sprint 3.1 verifies evidence and critic retry behavior. `safety_check` and
`confidence_check` are still placeholders owned by later sprint work, so they
are not independent production safety gates yet.

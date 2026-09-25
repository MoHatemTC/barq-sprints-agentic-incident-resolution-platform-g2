from typing import TypedDict, Any, List, Dict, Optional

class AgentState(TypedDict, total=False):
    incident_payload: Dict[str, Any]
    retrieved_evidence: List[Dict[str, Any]]
    retrieval_failed: bool
    classification: Optional[str]
    risk: Optional[str]
    confidence: Optional[float]
    outputs: Dict[str, Any]
    execution_id: str
    incident_number: str
    action_taken: Optional[str]
    human_review_required: bool
    failure_reason: Optional[str]

    # --- S3.1 additions ---
    # Structured verdict produced by the Critic/Verifier Agent.
    # Keys: passed (bool), feedback (str), invalid_steps (list[int]),
    #       citation_findings (list[dict])
    critic_verdict: Optional[Dict[str, Any]]

    # Number of revision cycles that have completed (initial=0, incremented on each
    # revision call to generate_node after a critic FAIL).
    revision_count: int

    # Set to True by graph routing when revision_count >= CRITIC_MAX_RETRIES.
    # Downstream sprints (S3.4) can inspect this to gate human escalation.
    critic_exhausted: bool

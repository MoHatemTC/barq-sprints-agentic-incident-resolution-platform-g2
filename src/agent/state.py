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

    #S3.4 human-in-the-loop
    # Which gate sent the run to human review
    gate: Optional[str]
    # Raw payload shown to the reviewer
    interrupt_payload: Dict[str, Any]
    # Reviewer-facing summary of the payload; None when generation failed
    approval_brief: Optional[Dict[str, str]]
    # Reviewer decision passed back through Command
    human_decision: Optional[Dict[str, Any]]
    # How the run continued: "human" after an approval decision
    resume_kind: Optional[str]
    # ServiceNow write outcome from act: written | already_done | skipped_no_sys_id
    servicenow_write: Optional[str]

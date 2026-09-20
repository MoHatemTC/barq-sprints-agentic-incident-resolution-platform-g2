from typing import TypedDict, Any, List, Dict, Optional

class AgentState(TypedDict, total=False):
    incident_payload: Dict[str, Any]
    retrieved_evidence: List[Dict[str, Any]]
    classification: Optional[str]
    risk: Optional[str]
    confidence: Optional[float]
    outputs: Dict[str, Any]
    execution_id: str
    incident_number: str
    action_taken: Optional[str]

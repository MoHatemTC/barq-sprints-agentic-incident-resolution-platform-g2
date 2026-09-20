from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="safety_check")
def safety_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through logic for now (enforcement completes in Sprint 4)."""
    return state

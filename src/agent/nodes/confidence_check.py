from typing import Dict, Any
from src.observability.tracing import trace_node


@trace_node(name="confidence_check")
def confidence_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Check confidence score against the floor threshold.

    Pass-through logic for now (enforcement completes in Sprint 4).
    Sets a default confidence value for routing.
    """
    return {"confidence": 0.95}

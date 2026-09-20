from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="diagnose", observation_type="generation")
def diagnose_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Diagnose the issue based on retrieved evidence."""
    outputs = state.get("outputs", {})
    outputs["diagnosis"] = "The router needs a reboot based on KB123."
    return {"outputs": outputs}

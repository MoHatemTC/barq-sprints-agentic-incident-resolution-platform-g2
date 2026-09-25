from typing import Dict, Any
from src.observability.tracing import trace_node

@trace_node(name="generate", observation_type="generation")
def generate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a candidate resolution as a numbered procedure citing source articles."""
    outputs = state.get("outputs", {})
    outputs["proposed_action"] = "update_incident"
    outputs["resolution"] = "1. Unplug router.\n2. Plug it back in. [Source: KB123]"
    return {"outputs": outputs}

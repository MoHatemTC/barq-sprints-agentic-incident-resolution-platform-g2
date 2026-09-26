from typing import Dict, Any
import logging
from src.observability.tracing import trace_node
from src.agent.guardrails.output_validation import validate_agent_output

logger = logging.getLogger(__name__)

@trace_node(name="safety_check")
def safety_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforces output guardrails before allowing write actions.
    If validation fails, zeroes out confidence to route to manual review.
    """
    result = validate_agent_output(state)
    
    if not result.is_valid:
        logger.warning(f"Output validation failed: {result.block_reasons}")
        state["confidence"] = 0.0
        state["action_taken"] = "blocked_by_guardrail"
        state["failure_reason"] = " | ".join(result.block_reasons)
        
    return state

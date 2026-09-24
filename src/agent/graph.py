from langgraph.graph import StateGraph, END
from src.agent.state import AgentState
from src.config import AGENT

from src.agent.nodes.load import load_node
from src.agent.nodes.validate import validate_node
from src.agent.nodes.classify import classify_node
from src.agent.nodes.determine_risk import determine_risk_node
from src.agent.nodes.retrieve import retrieve_node
from src.agent.nodes.diagnose import diagnose_node
from src.agent.nodes.generate import generate_node
from src.agent.nodes.verify_evidence import verify_evidence_node
from src.agent.nodes.safety_check import safety_check_node
from src.agent.nodes.confidence_check import confidence_check_node
from src.agent.nodes.interrupt import interrupt_node
from src.agent.nodes.act import act_node

CONFIDENCE_FLOOR = 0.6


def route_after_validate(state: AgentState) -> str:
    """Keep invalid or out-of-scope tickets out of the automated resolution path."""
    outputs = state.get("outputs") or {}
    if outputs.get("eligibility") != "valid":
        return "interrupt"
    return "classify"


def route_after_risk(state: AgentState) -> str:
    """Route high-risk incidents to interrupt (human path) without retrieval."""
    if state.get("risk") == "high":
        return "interrupt"
    return "retrieve"

def route_after_retrieve(state: AgentState) -> str:
    """No evidence (empty or retrieval failed) -> human path, else continue."""
    if not state.get("retrieved_evidence"):
        return "interrupt"
    return "diagnose"

def route_after_confidence(state: AgentState) -> str:
    """Route low-confidence results to interrupt; high-confidence to act."""
    confidence = state.get("confidence", 0.0)
    if confidence < CONFIDENCE_FLOOR:
        return "interrupt"
    return "act"


def route_after_critic(state: AgentState) -> str:
    """
    S3.1 deterministic routing after Critic/Verifier Agent.

    - PASS  → safety_check (normal path continues)
    - FAIL + retries remain → generate (Resolution Agent revision)
    - FAIL + retries exhausted → act (degraded path; critic_exhausted=True in state)

    Routing is determined entirely in Python from state fields.
    No LLM is involved in this decision.
    """
    verdict = state.get("critic_verdict") or {}
    if verdict.get("passed"):
        return "safety_check"
    if state.get("critic_exhausted"):
        return "act"
    return "generate"


def _mark_exhausted_if_needed(state: AgentState) -> dict:
    """
    Inline node: check whether the revision limit has been reached BEFORE
    routing back to generate_node.  If revision_count >= CRITIC_MAX_RETRIES,
    set critic_exhausted=True so that route_after_critic routes to act on the
    next evaluation.

    This node runs only on the FAIL path (i.e. after a non-passing critic verdict).
    It is transparent — it returns only the fields it may change.
    """
    revision_count = state.get("revision_count", 0)
    if revision_count >= AGENT.critic_max_retries:
        return {"critic_exhausted": True}
    return {"critic_exhausted": False}


def create_graph():
    workflow = StateGraph(AgentState)

    # Add all 11 nodes + interrupt + S3.1 exhaustion-check helper
    workflow.add_node("load", load_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("determine_risk", determine_risk_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("diagnose", diagnose_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("verify_evidence", verify_evidence_node)
    workflow.add_node("check_exhaustion", _mark_exhausted_if_needed)  # S3.1
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("confidence_check", confidence_check_node)
    workflow.add_node("interrupt", interrupt_node)
    workflow.add_node("act", act_node)

    # Entry point
    workflow.set_entry_point("load")

    # Validate gates automated work: invalid/out-of-scope tickets go to review.
    workflow.add_edge("load", "validate")
    workflow.add_conditional_edges(
        "validate",
        route_after_validate,
        {"classify": "classify", "interrupt": "interrupt"},
    )
    workflow.add_edge("classify", "determine_risk")

    workflow.add_conditional_edges(
        "determine_risk",
        route_after_risk,
        {"retrieve": "retrieve", "interrupt": "interrupt"},
    )

    workflow.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"diagnose": "diagnose", "interrupt": "interrupt"},
    )

    # Diagnostic Agent -> Resolution Agent (initial)
    workflow.add_edge("diagnose", "generate")

    # Resolution Agent -> Critic
    workflow.add_edge("generate", "verify_evidence")

    # Critic -> exhaustion check (always, even on PASS — cheap no-op on PASS path)
    workflow.add_edge("verify_evidence", "check_exhaustion")

    # After exhaustion check: route based on critic verdict + exhaustion flag
    workflow.add_conditional_edges(
        "check_exhaustion",
        route_after_critic,
        {
            "generate": "generate",           # FAIL + retries remain → revise
            "safety_check": "safety_check",   # PASS → normal path
            "act": "act",                      # FAIL + exhausted → degraded act
        },
    )

    workflow.add_edge("safety_check", "confidence_check")

    # Conditional edge after confidence: below floor -> interrupt, above -> act
    workflow.add_conditional_edges(
        "confidence_check",
        route_after_confidence,
        {
            "interrupt": "interrupt",
            "act": "act",
        },
    )

    workflow.add_edge("interrupt", END)
    workflow.add_edge("act", END)

    return workflow


def compile_graph(checkpointer=None):
    workflow = create_graph()
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)

    return workflow.compile()


from langgraph.graph import StateGraph, END
from src.agent.state import AgentState

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
    - PASS  -> safety_check
    - FAIL + retries remain -> generate
    - FAIL + retries exhausted -> act
    """
    verdict = state.get("critic_verdict") or {}
    if verdict.get("passed"):
        return "safety_check"
    if state.get("critic_exhausted"):
        return "act"
    return "generate"


def create_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("load", load_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("determine_risk", determine_risk_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("diagnose", diagnose_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("verify_evidence", verify_evidence_node)
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("confidence_check", confidence_check_node)
    workflow.add_node("interrupt", interrupt_node)
    workflow.add_node("act", act_node)

    workflow.set_entry_point("load")

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

    # S3.1 Internal multi-agent boundary
    workflow.add_edge("diagnose", "generate")
    workflow.add_edge("generate", "verify_evidence")

    # The conditional retry routing stays inside the boundary
    workflow.add_conditional_edges(
        "verify_evidence",
        route_after_critic,
        {
            "generate": "generate",
            "safety_check": "safety_check",
            "act": "act",
        },
    )

    workflow.add_edge("safety_check", "confidence_check")

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

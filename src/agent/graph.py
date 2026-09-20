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


def route_after_risk(state: AgentState) -> str:
    """Route high-risk incidents to interrupt (human path) without retrieval."""
    if state.get("risk") == "high":
        return "interrupt"
    return "retrieve"


def route_after_confidence(state: AgentState) -> str:
    """Route low-confidence results to interrupt; high-confidence to act."""
    confidence = state.get("confidence", 0.0)
    if confidence < CONFIDENCE_FLOOR:
        return "interrupt"
    return "act"


def create_graph():
    workflow = StateGraph(AgentState)

    # Add all 11 nodes + interrupt

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

    # Entry point
    workflow.set_entry_point("load")

    # Standard sequence: load -> validate -> classify -> determine_risk
    workflow.add_edge("load", "validate")
    workflow.add_edge("validate", "classify")
    workflow.add_edge("classify", "determine_risk")

    # Conditional edge after risk: high risk -> interrupt, normal -> retrieve
    workflow.add_conditional_edges(
        "determine_risk",
        route_after_risk,
        {
            "retrieve": "retrieve",
            "interrupt": "interrupt",
        },
    )

    # Automated path: retrieve -> diagnose -> generate -> verify -> safety -> confidence
    workflow.add_edge("retrieve", "diagnose")
    workflow.add_edge("diagnose", "generate")
    workflow.add_edge("generate", "verify_evidence")
    workflow.add_edge("verify_evidence", "safety_check")
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

    # Both interrupt and act terminate the graph
    workflow.add_edge("interrupt", END)
    workflow.add_edge("act", END)

    return workflow


def compile_graph(checkpointer=None):
    workflow = create_graph()
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()

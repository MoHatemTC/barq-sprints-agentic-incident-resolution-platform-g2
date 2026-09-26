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

from src.agent.knowledge_capture import capture_human_resolution
from src.db.database import SessionLocal


CONFIDENCE_FLOOR = 0.6


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


def route_after_interrupt(state: AgentState) -> str:
    """
    Approved human resolution -> knowledge capture.

    Rejected/manual decisions do not create a KB article.
    """
    human_decision = state.get("human_decision")

    if human_decision == "approve" and state.get("human_solution"):
        return "knowledge_capture"

    return "end"


def knowledge_capture_node(state: AgentState) -> AgentState:
    """
    Capture an approved human resolution as knowledge.

    Reuses the existing S3.5 knowledge-capture pipeline:
        Article Composer
        -> canonical Article
        -> ServiceNow publish + verification
        -> Qdrant synchronization
        -> audit
    """

    human_solution = state.get("human_solution")

    if not human_solution:
        raise ValueError(
            "Cannot perform knowledge capture without human_solution"
        )

    execution_id = state.get("execution_id")

    if not execution_id:
        raise ValueError(
            "Cannot perform knowledge capture without execution_id"
        )

    incident_payload = state.get("incident_payload") or {}
    incident_number = state.get("incident_number") or "UNKNOWN"

    # Reuse the existing classification when available.
    category = (
        state.get("classification")
        or incident_payload.get("category")
        or "general"
    )

    service = (
        incident_payload.get("service")
        or incident_payload.get("business_service")
        or "general"
    )

    # Use a deterministic article number tied to this execution.
    article_number = f"KBHR-{execution_id}"

    db = SessionLocal()

    try:
        result = capture_human_resolution(
            incident_snapshot=incident_payload,
            human_solution=human_solution,
            article_number=article_number,
            category=category,
            service=service,
            security_level="internal",
            execution_identifier=execution_id,
            db=db,
        )

    finally:
        db.close()

    return {
        "action_taken": "knowledge_captured",
        "knowledge_capture_result": result,
    }


def create_graph():
    workflow = StateGraph(AgentState)

    # Core agent nodes
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

    # Human escalation
    workflow.add_node("interrupt", interrupt_node)

    # Automated action
    workflow.add_node("act", act_node)

    # S3.5 knowledge capture
    workflow.add_node(
        "knowledge_capture",
        knowledge_capture_node,
    )

    # Entry point
    workflow.set_entry_point("load")

    # Standard path
    workflow.add_edge("load", "validate")
    workflow.add_edge("validate", "classify")
    workflow.add_edge("classify", "determine_risk")

    # Risk routing
    workflow.add_conditional_edges(
        "determine_risk",
        route_after_risk,
        {
            "retrieve": "retrieve",
            "interrupt": "interrupt",
        },
    )

    # Retrieval routing
    workflow.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {
            "diagnose": "diagnose",
            "interrupt": "interrupt",
        },
    )

    # Diagnosis path
    workflow.add_edge("diagnose", "generate")
    workflow.add_edge("generate", "verify_evidence")
    workflow.add_edge("verify_evidence", "safety_check")
    workflow.add_edge("safety_check", "confidence_check")

    # Confidence routing
    workflow.add_conditional_edges(
        "confidence_check",
        route_after_confidence,
        {
            "interrupt": "interrupt",
            "act": "act",
        },
    )

    # Human approval path:
    #
    # interrupt
    #    ↓
    # approved + human_solution
    #    ↓
    # knowledge_capture
    #    ↓
    # END
    #
    # rejected / missing solution
    #    ↓
    # END
    workflow.add_conditional_edges(
        "interrupt",
        route_after_interrupt,
        {
            "knowledge_capture": "knowledge_capture",
            "end": END,
        },
    )

    workflow.add_edge("knowledge_capture", END)

    # Normal automated path
    workflow.add_edge("act", END)

    return workflow


def compile_graph(checkpointer=None):
    workflow = create_graph()

    if checkpointer:
        return workflow.compile(
            checkpointer=checkpointer
        )

    return workflow.compile()
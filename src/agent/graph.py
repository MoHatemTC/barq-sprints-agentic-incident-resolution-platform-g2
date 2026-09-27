<<<<<<< HEAD
from langgraph.graph import StateGraph, END

=======
﻿from langgraph.graph import StateGraph, END
>>>>>>> origin/development
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
from src.agent.nodes.prepare_review import prepare_review_node
from src.agent.nodes.interrupt import interrupt_node
from src.agent.nodes.act import act_node

from src.agent.tools.registry import (
    DEFAULT_TOOL_REGISTRY,
    ToolRefusal,
)
from src.db.database import SessionLocal


CONFIDENCE_FLOOR = 0.6

def route_after_validate(state: AgentState) -> str:
    """Invalid or out-of-scope tickets go to human review before classify/determine_risk spend LLM calls."""
    outputs = state.get("outputs") or {}
    if outputs.get("eligibility") == "invalid":
        return "prepare_review"
    return "classify"

def route_after_risk(state: AgentState) -> str:
    """Route high-risk incidents to human review without retrieval."""
    if state.get("risk") == "high":
        return "prepare_review"
    return "retrieve"


def route_after_retrieve(state: AgentState) -> str:
    """Missing evidence (empty or retrieval failed) -> human review before diagnosis begins."""
    if not state.get("retrieved_evidence"):
        return "prepare_review"
    return "diagnose"


def route_after_confidence(state: AgentState) -> str:
    """Low confidence, a guardrail block (S3.3) or an exhausted critic (S3.1)
    -> human review; otherwise act."""
    if state.get("critic_exhausted") or state.get("action_taken") == "blocked_by_guardrail":
        return "prepare_review"
    confidence = state.get("confidence", 0.0)

    if confidence < CONFIDENCE_FLOOR:
<<<<<<< HEAD
        return "interrupt"

=======
        return "prepare_review"
>>>>>>> origin/development
    return "act"

def route_after_critic(state: AgentState) -> str:
    """
    S3.1 deterministic routing after Critic/Verifier Agent.
    - PASS  -> safety_check
    - FAIL + retries remain -> generate
    - FAIL + retries exhausted -> prepare_review (S3.4: a human decides, no unreviewed write)
    Routing is purely Python — no LLM involved.
    """
    verdict = state.get("critic_verdict") or {}
    if verdict.get("passed"):
        return "safety_check"
    if state.get("critic_exhausted"):
        return "prepare_review"
    return "generate"


def route_after_interrupt(state: AgentState) -> str:
    """
    Approved human resolution -> knowledge capture.

    Rejected/manual decisions do not create a KB article.

    The actual HIGH_RISK authorization is enforced by ToolRegistry.dispatch
    inside knowledge_capture_node.
    """
    human_decision = state.get("human_decision")

    if human_decision == "approve" and state.get("human_solution"):
        return "knowledge_capture"

    return "end"


def knowledge_capture_node(state: AgentState) -> AgentState:
    """
    Capture an approved human resolution as knowledge.

    The KB write-back MUST go through ToolRegistry so the HIGH_RISK
    approval gate is enforced before the existing S3.5 pipeline runs.

    Flow:
        ToolRegistry.dispatch("kb_write_back")
            -> HIGH_RISK approval check / consumption
            -> Article Composer
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
        result = DEFAULT_TOOL_REGISTRY.dispatch(
            "kb_write_back",
            execution_id=execution_id,
            incident_snapshot=incident_payload,
            human_solution=human_solution,
            article_number=article_number,
            category=category,
            service=service,
            security_level="human_resolution",
            db=db,
        )

        if isinstance(result, ToolRefusal):
            raise PermissionError(
                f"KB write-back refused: {result.reason}: {result.message}"
            )

    finally:
        db.close()

    return {
        "action_taken": "knowledge_captured",
        "knowledge_capture_result": result,
    }


def create_graph():
    workflow = StateGraph(AgentState)

<<<<<<< HEAD
    # Core agent nodes
=======
>>>>>>> origin/development
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
<<<<<<< HEAD

    # Human escalation
=======
    workflow.add_node("prepare_review", prepare_review_node)
>>>>>>> origin/development
    workflow.add_node("interrupt", interrupt_node)

    # Automated action
    workflow.add_node("act", act_node)

<<<<<<< HEAD
    # S3.5 knowledge capture
    workflow.add_node(
        "knowledge_capture",
        knowledge_capture_node,
    )

    # Entry point
    workflow.set_entry_point("load")

    # Standard path
=======
    workflow.set_entry_point("load")

>>>>>>> origin/development
    workflow.add_edge("load", "validate")
    workflow.add_conditional_edges(
        "validate",
        route_after_validate,
        {"classify": "classify", "prepare_review": "prepare_review"},
    )
    workflow.add_edge("classify", "determine_risk")

    # Risk routing
    workflow.add_conditional_edges(
        "determine_risk",
        route_after_risk,
<<<<<<< HEAD
        {
            "retrieve": "retrieve",
            "interrupt": "interrupt",
        },
=======
        {"retrieve": "retrieve", "prepare_review": "prepare_review"},
>>>>>>> origin/development
    )

    # Retrieval routing
    workflow.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
<<<<<<< HEAD
        {
            "diagnose": "diagnose",
            "interrupt": "interrupt",
        },
    )

    # Diagnosis path
=======
        {"diagnose": "diagnose", "prepare_review": "prepare_review"},
    )

>>>>>>> origin/development
    workflow.add_edge("diagnose", "generate")
    workflow.add_edge("generate", "verify_evidence")

    workflow.add_conditional_edges(
        "verify_evidence",
        route_after_critic,
        {
            "generate": "generate",
            "safety_check": "safety_check",
            "prepare_review": "prepare_review",
        },
    )

    workflow.add_edge("safety_check", "confidence_check")

<<<<<<< HEAD
    # Confidence routing
=======
    # Conditional edge after confidence: needs a human -> prepare_review, else act
>>>>>>> origin/development
    workflow.add_conditional_edges(
        "confidence_check",
        route_after_confidence,
        {
            "prepare_review": "prepare_review",
            "act": "act",
        },
    )

<<<<<<< HEAD
    # Human approval path:
    #
    # interrupt
    #    ↓
    # approved + human_solution
    #    ↓
    # knowledge_capture
    #    ↓
    # ToolRegistry HIGH_RISK gate
    #    ↓
    # existing knowledge-capture pipeline
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
=======
    # S3.4: human path pauses at interrupt() and resumes into act
    workflow.add_edge("prepare_review", "interrupt")
    workflow.add_edge("interrupt", "act")
>>>>>>> origin/development
    workflow.add_edge("act", END)

    return workflow


def compile_graph(checkpointer=None):
    workflow = create_graph()

    if checkpointer:
<<<<<<< HEAD
        return workflow.compile(
            checkpointer=checkpointer
        )

    return workflow.compile()
=======
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()
>>>>>>> origin/development

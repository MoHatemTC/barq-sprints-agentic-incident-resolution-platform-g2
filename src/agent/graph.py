import hashlib
import logging

from langgraph.graph import StateGraph, END
from src.agent.state import AgentState

from src.agent.nodes.load import load_node
from src.agent.nodes.validate import validate_node
from src.agent.nodes.classify import classify_node
from src.agent.nodes.determine_risk import determine_risk_node
from src.agent.nodes.formulate_query import formulate_query_node
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
from src.observability.tracing import trace_node

logger = logging.getLogger(__name__)


CONFIDENCE_FLOOR = 0.6

def route_after_validate(state: AgentState) -> str:
    """Invalid or out-of-scope tickets go to human review before classify/determine_risk spend LLM calls."""
    outputs = state.get("outputs") or {}
    if outputs.get("eligibility") == "invalid":
        return "prepare_review"
    return "classify"

def route_after_risk(state: AgentState) -> str:
    """Route high-risk incidents to human review before automated action.

    After a human approval the gate is already satisfied — do not re-open it
    when the re-classification loop passes through determine_risk a second time.
    """
    decision = state.get("human_decision") or {}
    if decision.get("decision") == "approve":
        return "formulate_query"
    if state.get("risk") == "high":
        return "prepare_review"
    return "formulate_query"


def route_after_retrieve(state: AgentState) -> str:
    """Missing evidence (empty or retrieval failed) -> human review before diagnosis begins."""
    if not state.get("retrieved_evidence"):
        # Already approved (high risk enrichment): never pause a second time.
        # act writes the reviewer's solution since there is no draft to enrich.
        decision = state.get("human_decision") or {}
        if decision.get("decision") == "approve":
            return "act"
        return "prepare_review"
    if state.get("retrieval_cache_hit") and state.get("cached_resolution"):
        # Reused KB text still must pass the normal output guardrail before it
        # can reach ServiceNow. Avoid LLM diagnosis/generation, not screening.
        return "safety_check"
    return "diagnose"


def route_after_confidence(state: AgentState) -> str:
    """Low confidence, a guardrail block (S3.3) or an exhausted critic (S3.1)
    -> human review; otherwise act."""
    # A human approval is the explicit override for this execution. Do not
    # reopen the same approval gate because the post-approval AI confidence is
    # below the normal automation floor; the reviewer already accepted the
    # human-provided resolution and the enriched result.
    if state.get("critic_exhausted") or state.get("action_taken") == "blocked_by_guardrail":
        # Do not reopen approval after a reviewer decision. act_node will use
        # the approved human solution instead of an unsafe/unverified draft.
        decision = state.get("human_decision") or {}
        if decision.get("decision") == "approve":
            return "act"
        return "prepare_review"
    decision = state.get("human_decision") or {}
    if decision.get("decision") == "approve":
        return "act"
    confidence = state.get("confidence", 0.0)

    if confidence < CONFIDENCE_FLOOR:
        return "prepare_review"
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


def route_after_human_review(state: AgentState) -> str:
    """After approval, enrich with KB evidence and generate resolution before writing."""

    decision = state.get("human_decision") or {}
    if (
        decision.get("decision") == "approve"
        and state.get("human_solution")
    ):
        return "classify"
    return "act"


def route_after_act(state: AgentState) -> str:

    decision = state.get("human_decision") or {}
    # A strong KB match means this is an existing problem. Reuse the existing
    # article and do not create another capture for the same incident pattern.
    if (
        decision.get("decision") == "approve"
        and state.get("human_solution")
        and not state.get("retrieval_cache_hit")
    ):
        return "knowledge_capture"
    return "end"


@trace_node(name="knowledge_capture")
def knowledge_capture_node(state: AgentState) -> AgentState:
    """
    Capture an approved human resolution as knowledge.

    The KB write-back MUST go through ToolRegistry so the HIGH_RISK
    approval gate is enforced before the existing pipeline runs.

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

    # Stable identity for the same incident pattern and reviewed resolution.
    # Using the execution id here created a new KB article for every retry.
    incident_text = (
        incident_payload.get("description")
        or incident_payload.get("short_description")
        or ""
    )
    kb_key = "|".join(
        part.strip().lower()
        for part in (incident_text, human_solution)
    )
    kb_fingerprint = hashlib.sha256(kb_key.encode("utf-8")).hexdigest()[:16]
    article_number = f"KBHR-{kb_fingerprint}"

    db = SessionLocal()


    try:
        result = DEFAULT_TOOL_REGISTRY.dispatch(
            "kb_write_back",
            execution_id,
            incident_snapshot=incident_payload,
            human_solution=human_solution,
            article_number=article_number,
            category=category,
            service=service,
            # The instance's security_level choice list has no "human_resolution"
            # (ServiceNow silently drops it, seen live on KB0010596); "internal" is what
            # the curated KB uses. Provenance stays in the KBHR- number and the audit table.
            security_level="internal",
            db=db,
        )
        if isinstance(result, ToolRefusal):
            error = f"KB write-back refused: {result.reason}: {result.message}"
        else:
            return {"action_taken": "knowledge_captured", "knowledge_capture_result": result}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        db.close()

    logger.error("Knowledge capture failed for %s: %s", execution_id, error)
    _record_failed_capture(execution_id, article_number, error)
    return {"knowledge_capture_result": {"status": "failed", "article_number": article_number, "error": error}}


def _record_failed_capture(execution_id: str, article_number: str, error: str) -> None:
    """knowledge_capture_audit row for a failed capture; never breaks the run."""
    from src.db.knowledge_capture_service import record_knowledge_capture

    db = SessionLocal()
    try:
        record_knowledge_capture(
            db=db,
            execution_reference=execution_id,
            status="failed",
            article_number=article_number,
            error=error[:2000],
        )
    except Exception as exc:
        logger.warning("Knowledge capture audit not written for %s: %s", execution_id, exc)
    finally:
        db.close()


def create_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("load", load_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("determine_risk", determine_risk_node)
    workflow.add_node("formulate_query", formulate_query_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("diagnose", diagnose_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("verify_evidence", verify_evidence_node)
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("confidence_check", confidence_check_node)
    workflow.add_node("prepare_review", prepare_review_node)
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
        {"formulate_query": "formulate_query", "prepare_review": "prepare_review"},
    )

    # Query formulation always goes to retrieve
    workflow.add_edge("formulate_query", "retrieve")

    # Retrieval routing
    workflow.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {
            "diagnose": "diagnose",
            "safety_check": "safety_check",
            "prepare_review": "prepare_review",
            "act": "act",
        },
    )

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

    # Conditional edge after confidence: needs a human -> prepare_review, else act
    workflow.add_conditional_edges(
        "confidence_check",
        route_after_confidence,
        {
            "prepare_review": "prepare_review",
            "act": "act",
        },
    )

    # High-risk approval resumes through retrieval and generation so the model
    # can combine the human solution with matching KB evidence before writing.
    workflow.add_edge("prepare_review", "interrupt")
    workflow.add_conditional_edges(
        "interrupt",
        route_after_human_review,
        {"classify": "classify", "act": "act"},
    )

    # act has written the outcome exactly once; an approved human
    # solution then becomes a KB article (ServiceNow + Qdrant)
    workflow.add_conditional_edges(
        "act",
        route_after_act,
        {"knowledge_capture": "knowledge_capture", "end": END},
    )
    workflow.add_edge("knowledge_capture", END)

    return workflow


def compile_graph(checkpointer=None):
    workflow = create_graph()

    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()

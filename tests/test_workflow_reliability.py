"""Deterministic reliability scenarios for the BARQ incident workflow.

These tests intentionally mock LLM calls. They exercise the safeguards that
must work even when an external model is slow, unavailable, or hallucinating.
"""

from unittest.mock import MagicMock, patch

from src.agent.graph import (
    route_after_critic,
    route_after_retrieve,
    route_after_risk,
    route_after_validate,
)
from src.agent.nodes.diagnose import _parse_diagnosis_response
from src.agent.nodes.retrieve import retrieve_node
from src.agent.nodes.verify_evidence import verify_evidence_node


EVIDENCE = [
    {
        "id": "KB-VPN-01",
        "text": "Clear a cached VPN credential after a password reset.",
        "score": 0.95,
    }
]


def _llm_response(content: str) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = content
    llm.invoke.return_value = response
    return llm


def test_reliable_cited_resolution_passes_verification():
    """A resolution backed by retrieved evidence can continue to safety checks."""
    llm = _llm_response(
        '{"passed": true, "feedback": "", "invalid_steps": [], '
        '"citation_findings": [{"step_num": 1, "citation_id": "KB-VPN-01", '
        '"found_in_evidence": true, "plausible": true, "reason": "Direct match."}]}'
    )
    with patch("src.agent.nodes.verify_evidence.get_llm", return_value=llm):
        result = verify_evidence_node(
            {
                "retrieved_evidence": EVIDENCE,
                "outputs": {
                    "resolution": "1. Clear the cached VPN credential. [Source: KB-VPN-01]"
                },
            }
        )

    assert result["critic_verdict"]["passed"] is True
    assert result["outputs"]["verification_passed"] is True


def test_hallucinated_citation_is_rejected_without_calling_llm():
    """An invented knowledge-base citation is blocked structurally."""
    llm = _llm_response('{"passed": true}')
    with patch("src.agent.nodes.verify_evidence.get_llm", return_value=llm):
        result = verify_evidence_node(
            {
                "retrieved_evidence": EVIDENCE,
                "outputs": {
                    "resolution": "1. Delete all VPN accounts. [Source: KB-FAKE-99]"
                },
            }
        )

    llm.invoke.assert_not_called()
    assert result["critic_verdict"]["passed"] is False
    assert 1 in result["critic_verdict"]["invalid_steps"]


def test_unrelated_question_routes_to_human_review_when_no_evidence_exists():
    """No retrieved KB evidence must not produce an automatic resolution."""
    assert route_after_retrieve({"retrieved_evidence": []}) == "interrupt"
    assert route_after_retrieve({"retrieved_evidence": None}) == "interrupt"


def test_invalid_eligibility_routes_to_human_review_before_retrieval():
    """An unrelated request is blocked before it can match an irrelevant KB."""
    assert route_after_validate({"outputs": {"eligibility": "invalid"}}) == "interrupt"
    assert route_after_validate({"outputs": {"eligibility": "valid"}}) == "classify"


def test_retrieval_outage_routes_to_human_review():
    """A search failure returns no evidence and exposes retrieval_failed for review."""
    with patch("src.agent.nodes.retrieve.search", side_effect=RuntimeError("Qdrant unavailable")):
        result = retrieve_node({"incident_payload": {"description": "VPN cannot connect"}})

    assert result == {"retrieved_evidence": [], "retrieval_failed": True}
    assert route_after_retrieve(result) == "interrupt"


def test_high_risk_request_skips_automatic_resolution():
    """High-risk requests route directly to the human-review interrupt path."""
    assert route_after_risk({"risk": "high"}) == "interrupt"


def test_malformed_agent_output_and_repeated_critic_failure_are_contained():
    """Malformed output is contained; exhausted retries remain explicitly marked."""
    diagnosis = _parse_diagnosis_response("not JSON from an unreliable model")

    assert diagnosis["root_cause"] == "not JSON from an unreliable model"
    assert diagnosis["supporting_evidence"] == []
    assert route_after_critic(
        {"critic_verdict": {"passed": False}, "critic_exhausted": True}
    ) == "act"

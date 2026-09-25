"""
S3.1 Diagnostic Agent node.

Responsibilities:
  - Analyse the retrieved evidence to determine the root cause.
  - Preserve the existing outputs["diagnosis"] string field.
  - Add outputs["diagnosis_structured"] with machine-readable fields.
  - Do NOT see or reference the draft resolution or Critic feedback.
  - Do NOT propose remediation steps.

Tracing: follows the same @trace_node / get_llm_callback() pattern
used by classify_node and determine_risk_node.
"""

import json
import logging
from typing import Any, Dict

from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm
from src.agent.prompts import DIAGNOSTIC_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Minimum confidence below which the diagnosis is flagged as unreliable.
# This value is written to outputs["diagnosis_structured"]["confidence"]
# and is later read by confidence_check (Sprint 4 will wire it to state.confidence).
_MIN_EVIDENCE_CONFIDENCE = 0.0
_MAX_EVIDENCE_CONFIDENCE = 1.0


def _format_evidence(retrieved_evidence: list[Dict[str, Any]]) -> str:
    """Format the retrieved-evidence list into a numbered text block for the LLM."""
    if not retrieved_evidence:
        return "(no evidence retrieved)"
    lines = []
    for i, ev in enumerate(retrieved_evidence, start=1):
        ev_id = ev.get("id", "UNKNOWN")
        ev_text = ev.get("text", "").strip()
        score = ev.get("score", 0.0)
        lines.append(f"[{i}] ID: {ev_id} (score: {score:.3f})\n{ev_text}")
    return "\n\n".join(lines)


def _calculate_confidence(
    retrieved_evidence: list[Dict[str, Any]],
    supporting_evidence: list[str],
) -> float:
    """
    Heuristic confidence score based on evidence quality.

    Base: mean retrieval score across all evidence chunks (0–1).
    Bonus: +0.10 if the LLM cited at least one evidence article.
    Clamped to [0.0, 1.0].
    """
    if not retrieved_evidence:
        return 0.0

    scores = [ev.get("score", 0.0) for ev in retrieved_evidence]
    # Retrieval scores from hybrid/rerank can exceed 1.0 (e.g. RRF values up to ~5).
    # Normalise by clamping each score to [0, 1] before averaging.
    normalised = [max(0.0, min(1.0, s)) for s in scores]
    base = sum(normalised) / len(normalised)

    bonus = 0.10 if supporting_evidence else 0.0
    return min(_MAX_EVIDENCE_CONFIDENCE, base + bonus)


def _parse_diagnosis_response(content: str) -> Dict[str, Any]:
    """
    Parse the LLM JSON response into a structured diagnosis dict.
    Falls back gracefully if the LLM does not return valid JSON.
    """
    # Strip markdown code fences if the LLM adds them despite instructions
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last fence lines
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
        return {
            "root_cause": str(data.get("root_cause", content)),
            "reasoning": str(data.get("reasoning", "")),
            "supporting_evidence": list(data.get("supporting_evidence", [])),
            "confidence": float(data.get("confidence", 0.5)),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Diagnostic Agent returned non-JSON response; using fallback parse.")
        return {
            "root_cause": content.strip(),
            "reasoning": "",
            "supporting_evidence": [],
            "confidence": 0.5,
        }


@trace_node(name="diagnose", observation_type="generation")
def diagnose_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Diagnostic Agent: determine root cause from retrieved evidence.

    Reads:
      state["retrieved_evidence"]  — list of {id, text, score} dicts
      state["incident_payload"]    — for incident description context

    Writes:
      outputs["diagnosis"]            — str  (root cause, backward-compatible)
      outputs["diagnosis_structured"] — dict (structured diagnostic info)
    """
    retrieved_evidence = state.get("retrieved_evidence") or []
    incident_payload = state.get("incident_payload", {})

    incident_desc = (
        incident_payload.get("description")
        or incident_payload.get("short_description")
        or "(no description provided)"
    )

    evidence_block = _format_evidence(retrieved_evidence)

    user_message = (
        f"INCIDENT DESCRIPTION:\n{incident_desc}\n\n"
        f"RETRIEVED EVIDENCE:\n{evidence_block}\n\n"
        "Diagnose the root cause based solely on the evidence above."
    )

    llm = get_llm()

    # Build the prompt using the dedicated Diagnostic system prompt.
    # Use the same invoke pattern as classify_node and determine_risk_node.
    full_prompt = f"{DIAGNOSTIC_SYSTEM_PROMPT}\n\n{user_message}"

    response = llm.invoke(full_prompt, config={"callbacks": get_llm_callback()})
    content = response.content if hasattr(response, "content") else str(response)

    structured = _parse_diagnosis_response(content)

    # Override the structured confidence with our heuristic if LLM gave something
    # unreasonable (e.g. MockLLM returning a flat string).
    heuristic_conf = _calculate_confidence(
        retrieved_evidence, structured.get("supporting_evidence", [])
    )
    # Blend: if the LLM returned a plausible confidence, average it with heuristic.
    llm_conf = structured.get("confidence", heuristic_conf)
    blended_conf = round((llm_conf + heuristic_conf) / 2.0, 4)
    structured["confidence"] = blended_conf

    outputs = state.get("outputs", {})
    outputs["diagnosis"] = structured["root_cause"]          # preserve string contract
    outputs["diagnosis_structured"] = structured              # new structured field

    logger.info(
        f"Diagnosis complete | confidence={blended_conf:.3f} "
        f"| cited={structured['supporting_evidence']}"
    )

    return {"outputs": outputs}

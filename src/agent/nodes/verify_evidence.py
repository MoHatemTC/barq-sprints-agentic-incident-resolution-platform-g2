"""
S3.1 Critic / Verifier Agent node.

Responsibilities:
  - Parse the resolution to extract all [Source: KB_ID] citations.
  - Verify each cited KB ID exists in retrieved_evidence.
  - Ask the LLM whether each cited evidence chunk plausibly supports its step.
  - Return a structured critic_verdict dict.
  - Do NOT rewrite or extend the resolution.
  - Do NOT pass if any citation is invalid or implausible.

Tracing: follows the same @trace_node / get_llm_callback() pattern used by all
other agent nodes in this graph.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm
from src.agent.prompts import CRITIC_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Regex for [Source: KB0001] or [Source: KB0001, KB0002] style citations.
# Captures the comma-separated IDs inside the brackets.
_CITATION_RE = re.compile(r"\[Source:\s*([^\]]+)\]", re.IGNORECASE)

# Regex for numbered resolution steps, e.g. "1. " or "1) "
_STEP_RE = re.compile(r"^(\d+)[.)]\s+(.+)$", re.MULTILINE)


def _extract_steps(resolution: str) -> List[Dict[str, Any]]:
    """
    Parse a numbered resolution into a list of step dicts.

    Each dict: {"step_num": int, "text": str, "citations": list[str]}
    Steps without citations get an empty citations list.
    """
    steps = []
    for m in _STEP_RE.finditer(resolution):
        step_num = int(m.group(1))
        text = m.group(2).strip()
        # Extract all citation IDs from this step's text
        citations = []
        for cite_match in _CITATION_RE.finditer(text):
            for raw_id in cite_match.group(1).split(","):
                stripped = raw_id.strip()
                if stripped:
                    citations.append(stripped)
        steps.append({"step_num": step_num, "text": text, "citations": citations})
    return steps


def _build_evidence_index(retrieved_evidence: List[Dict[str, Any]]) -> Dict[str, str]:
    """Return {id: text} for all retrieved evidence chunks."""
    return {ev.get("id", ""): ev.get("text", "") for ev in retrieved_evidence if ev.get("id")}


def _parse_critic_response(content: str) -> Dict[str, Any]:
    """
    Parse the LLM JSON verdict. Falls back to a safe FAIL verdict on parse error.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
        return {
            "passed": bool(data.get("passed", False)),
            "feedback": str(data.get("feedback", "")),
            "invalid_steps": [int(s) for s in data.get("invalid_steps", [])],
            "citation_findings": list(data.get("citation_findings", [])),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Critic Agent returned non-JSON response; treating as FAIL.")
        return {
            "passed": False,
            "feedback": f"Critic returned unparseable response: {content[:200]}",
            "invalid_steps": [],
            "citation_findings": [],
        }


def _structural_check(
    steps: List[Dict[str, Any]],
    evidence_index: Dict[str, str],
) -> tuple[bool, List[int], List[Dict[str, Any]]]:
    """
    Deterministic (no-LLM) structural pre-check:
      - Flag steps with no citations at all.
      - Flag steps citing KB IDs not present in retrieved_evidence.

    Returns (all_structural_ok, invalid_step_nums, findings)
    """
    all_ok = True
    invalid_steps: List[int] = []
    findings: List[Dict[str, Any]] = []

    for step in steps:
        step_num = step["step_num"]
        citations = step["citations"]

        if not citations:
            # Step has no citation — deterministically invalid
            all_ok = False
            invalid_steps.append(step_num)
            findings.append({
                "step_num": step_num,
                "citation_id": "MISSING",
                "found_in_evidence": False,
                "plausible": False,
                "reason": "Step has no citation.",
            })
            continue

        for cid in citations:
            found = cid in evidence_index
            if not found:
                all_ok = False
                if step_num not in invalid_steps:
                    invalid_steps.append(step_num)
            findings.append({
                "step_num": step_num,
                "citation_id": cid,
                "found_in_evidence": found,
                "plausible": found,  # plausibility checked by LLM below if found
                "reason": "" if found else f"KB ID '{cid}' not in retrieved evidence.",
            })

    return all_ok, invalid_steps, findings


@trace_node(name="verify_evidence", observation_type="generation")
def verify_evidence_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Critic/Verifier Agent: validate citations in the resolution against retrieved evidence.

    Reads:
      state["outputs"]["resolution"]   — str (the draft from Resolution Agent)
      state["retrieved_evidence"]      — list of {id, text, score}

    Writes:
      critic_verdict: {passed, feedback, invalid_steps, citation_findings}
      outputs["verification_passed"]   — bool mirror of critic_verdict["passed"]
      critic_exhausted                 — bool (set by graph routing, not this node)
    """
    outputs: Dict[str, Any] = state.get("outputs") or {}
    retrieved_evidence: List[Dict[str, Any]] = state.get("retrieved_evidence") or []

    resolution = outputs.get("resolution", "")
    if not resolution.strip():
        # No resolution to verify — fail immediately
        verdict = {
            "passed": False,
            "feedback": "No resolution draft was produced.",
            "invalid_steps": [],
            "citation_findings": [],
        }
        new_outputs = dict(outputs)
        new_outputs["verification_passed"] = False
        return {"critic_verdict": verdict, "outputs": new_outputs}

    evidence_index = _build_evidence_index(retrieved_evidence)
    steps = _extract_steps(resolution)

    if not steps:
        # Resolution exists but has no numbered steps — fail
        verdict = {
            "passed": False,
            "feedback": "Resolution contains no numbered steps.",
            "invalid_steps": [],
            "citation_findings": [],
        }
        new_outputs = dict(outputs)
        new_outputs["verification_passed"] = False
        return {"critic_verdict": verdict, "outputs": new_outputs}

    # --- Step 1: deterministic structural check (no LLM) ---
    structural_ok, structural_invalid, structural_findings = _structural_check(
        steps, evidence_index
    )

    # --- Step 2: plausibility check via LLM (only if structure passed) ---
    if structural_ok:
        evidence_block = "\n\n".join(
            f"ID: {eid}\n{etext}" for eid, etext in evidence_index.items()
        )
        user_message = (
            f"RETRIEVED EVIDENCE:\n{evidence_block}\n\n"
            f"DRAFT RESOLUTION:\n{resolution}\n\n"
            "Verify every citation. Respond with JSON only."
        )
        full_prompt = f"{CRITIC_SYSTEM_PROMPT}\n\n{user_message}"

        llm = get_llm()
        response = llm.invoke(full_prompt, config={"callbacks": get_llm_callback()})
        content = response.content if hasattr(response, "content") else str(response)
        verdict = _parse_critic_response(content)
    else:
        # Structural failure — skip LLM call, build verdict from structural findings
        verdict = {
            "passed": False,
            "feedback": (
                f"Citation structure invalid. Steps with issues: "
                f"{structural_invalid}. "
                "Each step must cite a KB article ID present in the retrieved evidence."
            ),
            "invalid_steps": structural_invalid,
            "citation_findings": structural_findings,
        }

    logger.info(
        f"Critic verdict: passed={verdict['passed']} "
        f"invalid_steps={verdict['invalid_steps']}"
    )

    new_outputs = dict(outputs)
    new_outputs["verification_passed"] = verdict["passed"]

    return {"critic_verdict": verdict, "outputs": new_outputs}

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm
from src.agent.prompts import CRITIC_SYSTEM_PROMPT
from src.config import AGENT

logger = logging.getLogger(__name__)

# Regex for [Source: KB0001] or [Source: KB0001, KB0002] style citations.
# Captures the comma-separated IDs inside the brackets.
_CITATION_RE = re.compile(r"\[Source:\s*([^\]]+)\]", re.IGNORECASE)

# Regex for numbered resolution steps, e.g. "1. " or "1) "
_STEP_RE = re.compile(r"^(\d+)[.)]\s+(.+)$", re.MULTILINE)


def _extract_steps(resolution: str) -> List[Dict[str, Any]]:
    steps = []
    for m in _STEP_RE.finditer(resolution):
        step_num = int(m.group(1))
        text = m.group(2).strip()
        citations = []
        for cite_match in _CITATION_RE.finditer(text):
            for raw_id in cite_match.group(1).split(","):
                stripped = raw_id.strip()
                if stripped:
                    citations.append(stripped)
        steps.append({"step_num": step_num, "text": text, "citations": citations})
    return steps


def _build_evidence_index(retrieved_evidence: List[Dict[str, Any]]) -> Dict[str, str]:
    return {ev.get("id", ""): ev.get("text", "") for ev in retrieved_evidence if ev.get("id")}


def _parse_critic_response(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("`" + ""):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "`" + "" else lines[1:])
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
    all_ok = True
    invalid_steps: List[int] = []
    findings: List[Dict[str, Any]] = []

    for step in steps:
        step_num = step["step_num"]
        citations = step["citations"]

        if not citations:
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
                "plausible": found,
                "reason": "" if found else f"KB ID '{cid}' not in retrieved evidence.",
            })

    return all_ok, invalid_steps, findings


@trace_node(name="verify_evidence", observation_type="generation")
def verify_evidence_node(state: Dict[str, Any]) -> Dict[str, Any]:
    outputs: Dict[str, Any] = state.get("outputs") or {}
    retrieved_evidence: List[Dict[str, Any]] = state.get("retrieved_evidence") or []
    
    # Internal check for exhaustion
    revision_count = state.get("revision_count", 0)
    critic_exhausted = (revision_count >= AGENT.critic_max_retries)

    resolution = outputs.get("resolution", "")
    if not resolution.strip():
        verdict = {
            "passed": False,
            "feedback": "No resolution draft was produced.",
            "invalid_steps": [],
            "citation_findings": [],
        }
        new_outputs = dict(outputs)
        new_outputs["verification_passed"] = False
        return {"critic_verdict": verdict, "outputs": new_outputs, "critic_exhausted": critic_exhausted}

    evidence_index = _build_evidence_index(retrieved_evidence)
    steps = _extract_steps(resolution)

    if not steps:
        verdict = {
            "passed": False,
            "feedback": "Resolution contains no numbered steps.",
            "invalid_steps": [],
            "citation_findings": [],
        }
        new_outputs = dict(outputs)
        new_outputs["verification_passed"] = False
        return {"critic_verdict": verdict, "outputs": new_outputs, "critic_exhausted": critic_exhausted}

    structural_ok, structural_invalid, structural_findings = _structural_check(
        steps, evidence_index
    )

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

    return {"critic_verdict": verdict, "outputs": new_outputs, "critic_exhausted": critic_exhausted}

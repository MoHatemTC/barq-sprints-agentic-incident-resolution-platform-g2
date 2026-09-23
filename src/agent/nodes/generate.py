"""
S3.1 Resolution Agent node.

Responsibilities:
  - Initial generation: diagnosis + evidence → numbered resolution with citations.
  - Revision generation: diagnosis + evidence + previous resolution + Critic feedback
    → targeted revision (NOT a blind rewrite).
  - Preserve the existing outputs["resolution"] string field.
  - Preserve the existing outputs["diagnosis"] — never overwrite it.
  - Increment revision_count each time this node runs after the initial call.

Tracing: follows the same @trace_node / get_llm_callback() pattern as all other
nodes in this graph.
"""

import json
import logging
from typing import Any, Dict, Optional

from src.observability.tracing import get_llm_callback, trace_node
from src.agent.llm import get_llm
from src.agent.prompts import RESOLUTION_SYSTEM_PROMPT, RESOLUTION_REVISION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _format_evidence(retrieved_evidence: list[Dict[str, Any]]) -> str:
    """Format evidence list into a numbered block for the LLM."""
    if not retrieved_evidence:
        return "(no evidence retrieved)"
    lines = []
    for i, ev in enumerate(retrieved_evidence, start=1):
        ev_id = ev.get("id", "UNKNOWN")
        ev_text = ev.get("text", "").strip()
        lines.append(f"[{i}] ID: {ev_id}\n{ev_text}")
    return "\n\n".join(lines)


def _build_initial_user_message(
    diagnosis: str,
    evidence_block: str,
) -> str:
    return (
        f"CONFIRMED DIAGNOSIS:\n{diagnosis}\n\n"
        f"RETRIEVED EVIDENCE:\n{evidence_block}\n\n"
        "Produce a numbered resolution procedure with citations."
    )


def _build_revision_user_message(
    diagnosis: str,
    evidence_block: str,
    previous_resolution: str,
    critic_feedback: str,
    invalid_steps: list[int],
) -> str:
    steps_str = ", ".join(str(s) for s in invalid_steps) if invalid_steps else "see feedback"
    return (
        f"CONFIRMED DIAGNOSIS:\n{diagnosis}\n\n"
        f"RETRIEVED EVIDENCE:\n{evidence_block}\n\n"
        f"PREVIOUS DRAFT RESOLUTION:\n{previous_resolution}\n\n"
        f"CRITIC FEEDBACK:\n{critic_feedback}\n\n"
        f"STEPS WITH CITATION PROBLEMS: {steps_str}\n\n"
        "Produce a corrected revision, fixing only the identified problems."
    )


@trace_node(name="generate", observation_type="generation")
def generate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolution Agent: produce or revise a numbered resolution procedure.

    Reads:
      state["outputs"]["diagnosis"]       — str  (root cause from Diagnostic Agent)
      state["retrieved_evidence"]         — list of {id, text, score}
      state["critic_verdict"]             — dict or None (present on revision calls)
      state["revision_count"]             — int  (0 on initial call)
      state["outputs"]["resolution"]      — str  (previous draft, present on revision)

    Writes:
      outputs["resolution"]               — str  (new or revised procedure)
      revision_count                      — int  (incremented if this is a revision)
    """
    retrieved_evidence: list = state.get("retrieved_evidence") or []
    outputs: Dict[str, Any] = state.get("outputs") or {}
    critic_verdict: Optional[Dict[str, Any]] = state.get("critic_verdict")
    revision_count: int = state.get("revision_count") or 0

    diagnosis = outputs.get("diagnosis", "(diagnosis not available)")
    evidence_block = _format_evidence(retrieved_evidence)

    is_revision = (
        critic_verdict is not None
        and not critic_verdict.get("passed", True)
        and bool(outputs.get("resolution"))
    )

    if is_revision:
        previous_resolution = outputs.get("resolution", "(no previous draft)")
        feedback = critic_verdict.get("feedback", "")
        invalid_steps = critic_verdict.get("invalid_steps", [])

        system_prompt = RESOLUTION_REVISION_SYSTEM_PROMPT
        user_message = _build_revision_user_message(
            diagnosis, evidence_block, previous_resolution, feedback, invalid_steps
        )
        logger.info(
            f"Resolution Agent: REVISION (revision_count={revision_count}, "
            f"invalid_steps={invalid_steps})"
        )
    else:
        system_prompt = RESOLUTION_SYSTEM_PROMPT
        user_message = _build_initial_user_message(diagnosis, evidence_block)
        logger.info("Resolution Agent: INITIAL generation")

    llm = get_llm()
    full_prompt = f"{system_prompt}\n\n{user_message}"
    response = llm.invoke(full_prompt, config={"callbacks": get_llm_callback()})
    content = response.content if hasattr(response, "content") else str(response)
    resolution = content.strip()

    # Write new resolution; preserve diagnosis (never overwrite it here)
    new_outputs = dict(outputs)
    new_outputs["resolution"] = resolution

    # Increment revision_count only when this is an actual revision call
    new_revision_count = revision_count + 1 if is_revision else revision_count

    return {
        "outputs": new_outputs,
        "revision_count": new_revision_count,
    }

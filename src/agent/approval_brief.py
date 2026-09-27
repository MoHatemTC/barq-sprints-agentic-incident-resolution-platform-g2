"""S3.4 Approval Brief Agent: a reviewer-facing summary of the interrupt payload , Presentation only"""

import json
import logging
from typing import Any, Dict, Optional

from src.agent.llm import get_llm
from src.agent.prompts import APPROVAL_BRIEF_PROMPT
from src.observability.tracing import get_llm_callback, trace_node

logger = logging.getLogger(__name__)

BRIEF_KEYS = ("what_happened", "why_stopped", "proposed_action", "reviewer_question")

INCIDENT_FIELDS = (
    "number", "short_description", "description", "category", "subcategory",
    "priority", "impact", "urgency", "business_service",
)
MAX_EVIDENCE = 5
MAX_EVIDENCE_CHARS = 600
MAX_FIELD_CHARS = 600


def _trim_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep what a reviewer needs, the full ServiceNow record is too large for the prompt"""
    incident = payload.get("incident") or {}
    return {
        "gate": payload.get("gate"),
        "reason": payload.get("reason_text"),
        "incident": {k: incident[k] for k in INCIDENT_FIELDS if incident.get(k)},
        "evidence": [
            {"id": e.get("id"), "text": str(e.get("text") or "")[:MAX_EVIDENCE_CHARS]}
            for e in (payload.get("evidence") or [])[:MAX_EVIDENCE]
        ],
        "draft": payload.get("draft"),
        "verdicts": payload.get("verdicts"),
    }


def build_prompt(payload: Dict[str, Any]) -> str:
    return APPROVAL_BRIEF_PROMPT.replace(
        "{payload}", json.dumps(_trim_payload(payload), indent=2, default=str)
    )


def parse_brief(text: str) -> Optional[Dict[str, str]]:
    """Strict parse: a JSON object with all four keys as non-empty strings, else None"""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    brief = {}
    for key in BRIEF_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        brief[key] = value.strip()[:MAX_FIELD_CHARS]
    return brief


@trace_node(name="approval_brief", observation_type="generation")
def generate_approval_brief(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """One LLM call; None on any failure so the reviewer falls back to the raw payload"""
    try:
        response = get_llm().invoke(
            build_prompt(payload), config={"callbacks": get_llm_callback()}
        )
        content = response.content if hasattr(response, "content") else str(response)
        brief = parse_brief(content)
        if brief is None:
            logger.warning("Approval brief unusable; falling back to raw payload")
        return brief
    except Exception as exc:
        logger.warning(f"Approval brief generation failed: {type(exc).__name__}: {exc}")
        return None

"""
Output Validation Guardrail (Sprint 3.3 — FR-18).

Validates agent outputs *before* they reach the act/write path.  This module
is consumed by ``safety_check_node`` to enforce semantic constraints on the
generated response and proposed actions.

Three validation layers:
  1. **Action Allowlist** — only pre-approved ServiceNow write operations may
     proceed; everything else is blocked. The allowlist is the ToolRegistry:
     unregistered tools are blocked; HIGH_RISK tools require human approval;
     READ and LOW_RISK_WRITE tools are permitted for automated execution.
     No production node currently sets outputs['proposed_action']; act_node
     only dispatches fixed tools. This layer is defense in depth for future
     agent-proposed actions.
  2. **Output Content Screening** — the generated text must not leak system
     internals, credentials, or injection artefacts that survived earlier
     stages.
  3. **Schema Conformance** — the ``outputs`` dict must carry the required
     keys before the agent is allowed to write back to ServiceNow.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Langfuse helpers (best-effort; never crash the pipeline)
# ---------------------------------------------------------------------------
try:
    from langfuse import get_client
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    get_client = None

try:
    from src.observability.tracing import _current_trace_id
except ImportError:
    _current_trace_id = None

# langfuse
def _emit_output_guardrail_span(
    *,
    name: str,
    verdict: str,
    details: Dict[str, Any],
    latency_ms: float,
) -> None:
    """Best-effort Langfuse span with ``as_type='guardrail'``."""
    if not LANGFUSE_AVAILABLE or get_client is None:
        return
    try:
        client = get_client()
        trace_id = _current_trace_id.get() if _current_trace_id else None
        trace_context = {"trace_id": trace_id} if trace_id else None

        span = client.start_observation(
            name=f"output_guardrail:{name}",
            as_type="guardrail",
            metadata={
                "verdict": verdict,
                "latency_ms": latency_ms,
                **details,
            },
            **(trace_context or {}),
        )
        span.end()
    except Exception:
        logger.debug("Langfuse output guardrail span emission failed", exc_info=True)


# ---------------------------------------------------------------------------
# 1. Action Allowlist (ToolRegistry-backed)
# ---------------------------------------------------------------------------

@dataclass
class ActionValidationResult:
    """Result of validating a proposed action against the ToolRegistry."""
    is_allowed: bool
    action: str
    reason: str


def validate_action(action: str) -> ActionValidationResult:
    """Check whether *action* is permitted by the ToolRegistry.

    Returns an ``ActionValidationResult`` with:
    - is_allowed=True for READ or LOW_RISK_WRITE tools
    - is_allowed=False for unregistered tools (reason="unregistered_tool")
    - is_allowed=False for HIGH_RISK tools (reason="requires_human_approval")

    The ToolRegistry is imported lazily to avoid circular imports at module load.
    """
    from src.agent.tools.registry import DEFAULT_TOOL_REGISTRY

    normalised = action.strip().lower().replace("-", "_").replace(" ", "_")
    permission_class = DEFAULT_TOOL_REGISTRY.permission_class_of(normalised)

    if permission_class is None:
        return ActionValidationResult(
            is_allowed=False,
            action=action,
            reason=f"Action '{action}' is not a registered tool (unregistered_tool).",
        )

    from src.agent.tools.permissions import PermissionClass
    if permission_class is PermissionClass.HIGH_RISK:
        return ActionValidationResult(
            is_allowed=False,
            action=action,
            reason=f"Action '{action}' requires human approval (HIGH_RISK).",
        )

    # READ and LOW_RISK_WRITE are allowed for automated execution
    return ActionValidationResult(
        is_allowed=True,
        action=action,
        reason=f"Action '{action}' is permitted ({permission_class.name}).",
    )


# ---------------------------------------------------------------------------
# 2. Output Content Screening
# ---------------------------------------------------------------------------

# Patterns that should NEVER appear in agent output text.
_OUTPUT_LEAK_PATTERNS: List[tuple] = [

    # System prompt / internal config leakage

    (re.compile(r"(?i)system\s*prompt\s*[:=]"), "system_prompt_leak"),
    (re.compile(r"(?i)(?:my|the)\s+(?:instructions?|directives?)\s+(?:are|is)\s*[:=]"), "instruction_leak"),
    (re.compile(r"(?i)(?:internal|hidden)\s+(?:configuration|config|settings?)"), "config_leak"),

    # Residual injection markers that should have been caught on input

    (re.compile(r"\[SCREENED_CONTENT\]"), "residual_injection_marker"),

    # Credential / secret leakage in output

    (re.compile(
        r"(?i)(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)"
        r"\s*[=:]\s*\S{8,}"
    ), "credential_in_output"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "bearer_token_in_output"),
    (re.compile(
        r"(?i)(?:password|passwd|pwd)(?:\s+is)?\s*[=:]\s*\S+"
    ), "password_in_output"),

    # Raw SQL / destructive commands

    (re.compile(
        r"(?i)(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE|ALTER\s+TABLE.*DROP)"
    ), "destructive_sql"),
    (re.compile(
        r"(?i)(?:rm\s+-rf|format\s+[a-z]:|del\s+/[sfq])"
    ), "destructive_shell_command"),
]


@dataclass
class OutputScreeningResult:
    """Result of scanning agent-generated output text."""
    is_clean: bool
    flagged_labels: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def screen_output_content(text: str) -> OutputScreeningResult:
    """Scan *text* for content that must never appear in agent output.

    Returns ``OutputScreeningResult`` with ``is_clean=False`` when any
    forbidden pattern is detected.
    """
    if not text or not text.strip():
        return OutputScreeningResult(is_clean=True)

    flagged: List[str] = []
    match_details: Dict[str, str] = {}

    for pattern, label in _OUTPUT_LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            flagged.append(label)
            # Store a truncated snippet (no raw secrets)
            snippet = match.group(0)
            match_details[label] = snippet[:40] + "..." if len(snippet) > 40 else snippet

    return OutputScreeningResult(
        is_clean=len(flagged) == 0,
        flagged_labels=flagged,
        details=match_details,
    )


# ---------------------------------------------------------------------------
# 3. Schema Conformance
# ---------------------------------------------------------------------------

# Keys that MUST be present in ``state["outputs"]`` before writing back.
REQUIRED_OUTPUT_KEYS: Set[str] = frozenset({
    "resolution",
})

# Keys that are optional but recommended.
RECOMMENDED_OUTPUT_KEYS: Set[str] = frozenset({
    "root_cause",
    "confidence_score",
    "evidence_refs",
})


@dataclass
class SchemaValidationResult:
    """Result of validating the outputs dict against the required schema."""
    is_valid: bool
    missing_keys: List[str] = field(default_factory=list)
    missing_recommended: List[str] = field(default_factory=list)


def validate_output_schema(outputs: Optional[Dict[str, Any]]) -> SchemaValidationResult:
    """Verify that *outputs* contains every required key before write-back.

    Missing *recommended* keys do not block the pipeline but are logged
    for observability.
    """
    if outputs is None:
        return SchemaValidationResult(
            is_valid=False,
            missing_keys=sorted(REQUIRED_OUTPUT_KEYS),
            missing_recommended=sorted(RECOMMENDED_OUTPUT_KEYS),
        )

    missing = sorted(k for k in REQUIRED_OUTPUT_KEYS if k not in outputs or not outputs[k])
    missing_rec = sorted(k for k in RECOMMENDED_OUTPUT_KEYS if k not in outputs)

    if missing_rec:
        logger.info("Output schema: recommended keys missing: %s", missing_rec)

    return SchemaValidationResult(
        is_valid=len(missing) == 0,
        missing_keys=missing,
        missing_recommended=missing_rec,
    )


# ---------------------------------------------------------------------------
# Composite Validation (consumed by safety_check_node later)
# ---------------------------------------------------------------------------

@dataclass
class OutputValidationResult:
    """Aggregated result of all output validation checks."""
    is_valid: bool
    action_result: Optional[ActionValidationResult] = None
    screening_result: Optional[OutputScreeningResult] = None
    schema_result: Optional[SchemaValidationResult] = None
    block_reasons: List[str] = field(default_factory=list)


def validate_agent_output(state: Dict[str, Any]) -> OutputValidationResult:
    """Run all output validation checks against the current agent state.

    This is the single entry-point that ``safety_check_node`` will call.
    It aggregates action allowlist, content screening, and schema validation
    into one ``OutputValidationResult``.
    """
    import time
    start = time.perf_counter()

    block_reasons: List[str] = []

    # --- 1. Action allowlist (ToolRegistry-backed) ----------------------------------
    # NOTE: Only outputs["proposed_action"] is checked. action_taken holds
    # outcome labels (e.g. "resolved_automatically") from a previous run and
    # is NOT a proposed tool name. It must not be used here.
    outputs = state.get("outputs") or {}
    proposed_action = outputs.get("proposed_action")
    action_result = None
    if proposed_action:
        action_result = validate_action(proposed_action)
        if not action_result.is_allowed:
            block_reasons.append(f"ACTION_BLOCKED: {action_result.reason}")

    # --- 2. Output content screening -----------------------------------------------
    resolution = outputs.get("resolution", "")
    screening_result = screen_output_content(resolution)
    if not screening_result.is_clean:
        block_reasons.append(
            f"OUTPUT_CONTENT_FLAGGED: {', '.join(screening_result.flagged_labels)}"
        )

    # --- 3. Schema conformance -----------------------------------------------------
    schema_result = validate_output_schema(outputs)
    if not schema_result.is_valid:
        block_reasons.append(
            f"SCHEMA_INVALID: missing required keys {schema_result.missing_keys}"
        )

    is_valid = len(block_reasons) == 0
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Emit observability span
    _emit_output_guardrail_span(
        name="validate_agent_output",
        verdict="pass" if is_valid else "block",
        details={
            "block_reasons": block_reasons,
            "action": proposed_action,
            "screening_flags": screening_result.flagged_labels if screening_result else [],
            "schema_missing": schema_result.missing_keys if schema_result else [],
        },
        latency_ms=elapsed_ms,
    )

    return OutputValidationResult(
        is_valid=is_valid,
        action_result=action_result,
        screening_result=screening_result,
        schema_result=schema_result,
        block_reasons=block_reasons,
    )
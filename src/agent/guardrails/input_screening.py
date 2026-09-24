import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

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


def _emit_guardrail_span(
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
            name=name,
            as_type="guardrail",
            trace_context=trace_context,
            input={"verdict": verdict},
            metadata={
                "latency_ms": round(latency_ms, 2),
                **{k: v for k, v in details.items() if k != "raw_text"},
            },
        )
        span.update(
            output={"verdict": verdict, **details},
            level="WARNING" if verdict != "pass" else "DEFAULT",
            status_message=verdict,
        )
        span.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Guardrail span emission failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Prompt-Injection Detection
# ═══════════════════════════════════════════════════════════════════════════

# Each pattern is a (compiled_regex, label) tuple.  The label is used in
# audit metadata and test assertions.

_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # --- Instruction override ---
    (re.compile(
        r"(?i)(?:ignore|disregard|forget|override)\s+"
        r"(?:all\s+)?(?:previous|above|prior|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|rules?|context|directives?|guidelines?)"
    ), "instruction_override"),

    (re.compile(
        r"(?i)(?:do\s+not\s+follow|stop\s+following)\s+"
        r"(?:the\s+)?(?:previous|above|system|original)\s+"
        r"(?:instructions?|prompts?|rules?)"
    ), "instruction_override"),

    (re.compile(
        r"(?i)(?:new\s+instructions?|updated?\s+instructions?|real\s+instructions?)\s*[:;]"
    ), "instruction_override"),

    # --- Role-play / persona hijack ---
    (re.compile(
        r"(?i)(?:you\s+are\s+now|act\s+as(?:\s+if\s+you\s+(?:are|were))?|"
        r"pretend\s+(?:to\s+be|you\s+are)|"
        r"from\s+now\s+on\s+you\s+are|"
        r"roleplay\s+as|behave\s+as)\s+"
        r"(?:a\s+|an\s+)?(?:DAN|jailbreak|unrestricted|unfiltered|evil|hacker|admin)"
    ), "roleplay_hijack"),

    (re.compile(
        r"(?i)(?:system\s*prompt|system\s*message)\s*[:=]"
    ), "roleplay_hijack"),

    # --- Delimiter / fence breaking ---
    (re.compile(
        r"(?:```|<\|(?:im_start|im_end|system|endoftext)\|>|<<\s*SYS\s*>>|"
        r"\[INST\]|\[/INST\]|<\|(?:user|assistant|end)\|>)"
    ), "delimiter_break"),

    (re.compile(
        r"(?i)---\s*(?:BEGIN|END)\s+(?:SYSTEM|HIDDEN|SECRET)\s+"
        r"(?:PROMPT|MESSAGE|INSTRUCTIONS?)\s*---"
    ), "delimiter_break"),

    # --- Data exfiltration / prompt leak ---
    (re.compile(
        r"(?i)(?:repeat|reveal|show|print|output|display|echo)\s+"
        r"(?:your\s+)?(?:system\s+)?(?:prompt|instructions?|rules?|context|configuration)"
    ), "prompt_leak"),
]

# A safe wrapper that neutralises injection content in the forwarded text.
_NEUTRALISE_PREFIX = "[SCREENED_CONTENT]"
_NEUTRALISE_SUFFIX = "[/SCREENED_CONTENT]"


@dataclass
class InjectionScreeningResult:
    """Result of prompt-injection screening on a single text field."""
    is_flagged: bool = False
    matched_labels: List[str] = field(default_factory=list)
    neutralised_text: str = ""


def screen_for_injection(text: str) -> InjectionScreeningResult:
    """
    Scan *text* for known prompt-injection patterns.

    Returns an ``InjectionScreeningResult`` whose ``neutralised_text``
    wraps every matched span in safe delimiters so downstream prompts
    treat the content as untrusted data rather than instructions.
    The original text is never dropped — incidents always proceed.
    """
    if not text or not text.strip():
        return InjectionScreeningResult(neutralised_text=text)

    matched_labels: List[str] = []
    # Collect all match spans for neutralisation
    spans: List[Tuple[int, int]] = []

    for pattern, label in _INJECTION_PATTERNS:
        for m in pattern.finditer(text):
            if label not in matched_labels:
                matched_labels.append(label)
            spans.append((m.start(), m.end()))

    if not spans:
        return InjectionScreeningResult(neutralised_text=text)

    # Merge overlapping spans
    spans.sort()
    merged: List[Tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Build neutralised text
    parts: List[str] = []
    prev = 0
    for start, end in merged:
        parts.append(text[prev:start])
        parts.append(f"{_NEUTRALISE_PREFIX}{text[start:end]}{_NEUTRALISE_SUFFIX}")
        prev = end
    parts.append(text[prev:])

    return InjectionScreeningResult(
        is_flagged=True,
        matched_labels=matched_labels,
        neutralised_text="".join(parts),
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Credential / PII Redaction
# ═══════════════════════════════════════════════════════════════════════════

# Each entry: (compiled_regex, replacement_token, redaction_type_label)
_REDACTION_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # --- API keys / generic secrets in config-like context ---
    # Matches key=value patterns where key suggests a secret
    (re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?key|auth[_-]?token|"
        r"private[_-]?key|client[_-]?secret|app[_-]?secret)"
        r"\s*[=:]\s*['\"]?([A-Za-z0-9\-_./+=]{8,})['\"]?"
    ), "[REDACTED_API_KEY]", "api_key"),

    # Bearer / token auth headers
    (re.compile(
        r"(?i)(?:bearer|token)\s+([A-Za-z0-9\-_./+=]{20,})"
    ), "[REDACTED_BEARER_TOKEN]", "bearer_token"),

    # Generic password fields  password = "..." / password: ...
    (re.compile(
        r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"]?(\S{4,})['\"]?"
    ), "[REDACTED_PASSWORD]", "password"),

    # --- PII ---
    # US Social Security Number  (XXX-XX-XXXX)
    (re.compile(
        r"\b(\d{3}-\d{2}-\d{4})\b"
    ), "[REDACTED_SSN]", "ssn"),

    # Credit card numbers (13-19 digits, optionally separated by dashes or spaces)
    (re.compile(
        r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,7})\b"
    ), "[REDACTED_CREDIT_CARD]", "credit_card"),

    # E-mail addresses
    (re.compile(
        r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
    ), "[REDACTED_EMAIL]", "email"),

    # Phone numbers — international or US-style
    (re.compile(
        r"(?<!\d)(\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4})(?!\d)"
    ), "[REDACTED_PHONE]", "phone"),
]


@dataclass
class RedactionResult:
    """Result of credential / PII redaction on a single text field."""
    redacted_text: str = ""
    redaction_count: int = 0
    redaction_types: List[str] = field(default_factory=list)


def redact_sensitive_content(text: str) -> RedactionResult:
    """
    Replace credentials and PII in *text* with deterministic tokens.

    Original sensitive values are **never** stored on the result object.
    Only aggregate counts and type labels are recorded for audit.
    """
    if not text or not text.strip():
        return RedactionResult(redacted_text=text)

    redacted = text
    count = 0
    types: List[str] = []

    for pattern, replacement, label in _REDACTION_PATTERNS:
        new_text, n = pattern.subn(replacement, redacted)
        if n > 0:
            count += n
            if label not in types:
                types.append(label)
            redacted = new_text

    return RedactionResult(
        redacted_text=redacted,
        redaction_count=count,
        redaction_types=types,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Composite screening function (used by load.py)
# ═══════════════════════════════════════════════════════════════════════════

# Fields from the incident payload that carry user-supplied text and must
# be screened.  Other fields (sys_id, priority, state) are structural and
# not screened.
_TEXT_FIELDS = ("description", "short_description", "comments", "work_notes", "close_notes")


@dataclass
class ScreeningMetadata:
    """Audit metadata recorded on the execution record.

    Contains **no** raw sensitive strings — only counts and type labels.
    """
    screened: bool = True
    injection_flagged: bool = False
    injection_labels: List[str] = field(default_factory=list)
    redaction_count: int = 0
    redaction_types: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    fields_screened: List[str] = field(default_factory=list)


def screen_incident_payload(
    payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], ScreeningMetadata]:
    """
    Run injection screening and PII redaction over the text fields of an
    incident payload.

    Returns
    -------
    (screened_payload, metadata)
        The screened payload with neutralised / redacted text, and an
        audit metadata object suitable for recording on the execution
        record.  The metadata contains **no** raw sensitive strings.

    The incident is **never** silently dropped.  Even if injection is
    detected, the neutralised content proceeds downstream.
    """
    start = time.perf_counter()
    payload = payload.copy()  # shallow copy — we replace str values
    meta = ScreeningMetadata()

    all_injection_labels: List[str] = []
    total_redactions = 0
    all_redaction_types: List[str] = []

    for field_name in _TEXT_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue

        meta.fields_screened.append(field_name)

        # Step 1: Injection screening
        inj = screen_for_injection(value)
        working_text = inj.neutralised_text
        if inj.is_flagged:
            meta.injection_flagged = True
            for lbl in inj.matched_labels:
                if lbl not in all_injection_labels:
                    all_injection_labels.append(lbl)

        # Step 2: PII / credential redaction
        red = redact_sensitive_content(working_text)
        total_redactions += red.redaction_count
        for t in red.redaction_types:
            if t not in all_redaction_types:
                all_redaction_types.append(t)

        # Replace the field with the cleaned text
        payload[field_name] = red.redacted_text

    meta.injection_labels = all_injection_labels
    meta.redaction_count = total_redactions
    meta.redaction_types = all_redaction_types

    elapsed_ms = (time.perf_counter() - start) * 1000
    meta.latency_ms = round(elapsed_ms, 2)

    # --- Langfuse guardrail span ---
    verdict = "flagged" if meta.injection_flagged or meta.redaction_count > 0 else "pass"
    _emit_guardrail_span(
        name="input_screening",
        verdict=verdict,
        details={
            "injection_flagged": meta.injection_flagged,
            "injection_labels": meta.injection_labels,
            "redaction_count": meta.redaction_count,
            "redaction_types": meta.redaction_types,
            "fields_screened": meta.fields_screened,
        },
        latency_ms=meta.latency_ms,
    )

    logger.info(
        "[input_screening] verdict=%s injection=%s redactions=%d latency=%.1fms",
        verdict,
        meta.injection_flagged,
        meta.redaction_count,
        meta.latency_ms,
    )

    return payload, meta

"""
Metadata pre-filters for retrieval (S2.4).
we have here Two pieces:
  RetrievalFilters : what we want (plain dataclass)
  build_qdrant_filter() : turns it into a Qdrant filter
"""

from dataclasses import dataclass, field

from qdrant_client import models

from ..config import RETRIEVAL


@dataclass(frozen=True)
class RetrievalFilters:
    # Optional: set any of these to narrow the search (None = don't filter on it).
    category: str | None = None
    service: str | None = None
    version: int | None = None
    # Always applied. Defaults come from .env / config.
    allowed_workflow_states: tuple[str, ...] = field(
        default_factory=lambda: RETRIEVAL.allowed_workflow_states
    )
    blocked_security_levels: tuple[str, ...] = field(
        default_factory=lambda: RETRIEVAL.blocked_security_levels
    )


def _equals(key: str, value) -> models.FieldCondition:
    """payload[key] == value"""
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _in(key: str, values) -> models.FieldCondition:
    """payload[key] in values"""
    return models.FieldCondition(key=key, match=models.MatchAny(any=list(values)))


def build_qdrant_filter(filters: RetrievalFilters | None = None) -> models.Filter:
    """must = all of these are true (AND). must_not = none of these are true."""
    f = filters or RetrievalFilters()

    must = [_in("workflow_state", f.allowed_workflow_states)]
    must_not = [_equals("_is_marker", True)]  # S1.4's fingerprint point is not content

    if f.blocked_security_levels:
        must_not.append(_in("security_level", f.blocked_security_levels))

    # Each optional field that is set adds one more AND condition.
    for key in ("category", "service", "version"):
        value = getattr(f, key)
        if value is not None:
            must.append(_equals(key, value))

    return models.Filter(must=must, must_not=must_not)

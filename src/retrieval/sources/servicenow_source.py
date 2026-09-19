"""
Reads published articles directly from ServiceNow's Knowledge Base via
S1.5's Table API read client, authenticated under S1.2's OAuth identity.

STATUS: stub -- to be wired up once S1.5's client is handed off.
Do not duplicate extraction logic here; this should call into S1.5's
existing client rather than reimplement Table API access.
"""

from ...config import SERVICENOW
from ..schema import Article


def _value(value):
    """Return the stored value from either Table API reference representation."""
    return value.get("value", "") if isinstance(value, dict) else value


def article_from_servicenow(record: dict) -> Article:
    """Map a configured kb_knowledge row into S1.4's canonical article shape.

    The fetch client is deliberately not implemented here. S1.5 currently
    exposes incident reads only, but this mapper fixes the contract that a
    future kb_knowledge reader must use: configured metadata column names map
    back to the canonical fields used by indexing.
    """
    metadata = {
        source_field: _value(record.get(servicenow_field, ""))
        for source_field, servicenow_field in SERVICENOW.kb_metadata_field_map.items()
    }
    article_number = metadata.get("article_number") or _value(record.get("number", ""))
    version = metadata.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1

    return Article(
        sys_id=_value(record.get("sys_id", "")),
        number=article_number,
        article_id=article_number,
        title=_value(record.get("short_description", "")),
        body=_value(record.get("text", "")),
        category=_value(record.get("kb_category", "")),
        service=metadata.get("service", ""),
        workflow_state=_value(record.get("workflow_state", "")),
        version=version,
        security_level=metadata.get("security_level", ""),
    )


def load_articles_from_servicenow() -> list[Article]:
    """
    TODO: replace with a call into S1.5's read client once handed off.
    Expected: fetch published kb_knowledge records, map fields to Article.
    """
    raise NotImplementedError(
        "ServiceNow article source not yet wired up -- waiting on S1.5 "
        "Table API read client hand-off. Use local_json_source for now."
    )

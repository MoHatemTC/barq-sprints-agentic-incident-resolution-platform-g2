"""
Reads published articles directly from ServiceNow's Knowledge Base via
S1.5's Table API read client, authenticated under S1.2's OAuth identity.

STATUS: stub -- to be wired up once S1.5's client is handed off.
Do not duplicate extraction logic here; this should call into S1.5's
existing client rather than reimplement Table API access.
"""

from ...config import SERVICENOW
from ..schema import Article
from functools import lru_cache

from ...config import PATHS
import json



def _value(value):
    """Return the stored value from either Table API reference representation."""
    return value.get("value", "") if isinstance(value, dict) else value

@lru_cache(maxsize=1)
def _category_names() -> dict[str, str]:
    """sys_id -> category name (reverse of kb_category_mapping.json)."""
    with open(PATHS.servicenow_kb_category_mapping, encoding="utf-8") as f:
        name_to_id = json.load(f)
    return {sys_id: name for name, sys_id in name_to_id.items()}


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
        category=_category_names().get(
            _value(record.get("kb_category", "")),
            _value(record.get("kb_category", "")),
        ),
        service=metadata.get("service", ""),
        workflow_state=_value(record.get("workflow_state", "")),
        version=version,
        security_level=metadata.get("security_level", ""),
    )


def load_articles_from_servicenow() -> list[Article]:
    """Fetch published kb_knowledge records and map them to Article."""
    from ...servicenow.client import ServiceNowClient

    records = ServiceNowClient().get_published_kb_articles()
    return [article_from_servicenow(r) for r in records]
"""
Reads published articles directly from ServiceNow's Knowledge Base via
S1.5's Table API read client, authenticated under S1.2's OAuth identity.

STATUS: stub -- to be wired up once S1.5's client is handed off.
Do not duplicate extraction logic here; this should call into S1.5's
existing client rather than reimplement Table API access.
"""

from ..schema import Article


def load_articles_from_servicenow() -> list[Article]:
    """
    TODO: replace with a call into S1.5's read client once handed off.
    Expected: fetch published kb_knowledge records, map fields to Article.
    """
    raise NotImplementedError(
        "ServiceNow article source not yet wired up -- waiting on S1.5 "
        "Table API read client hand-off. Use local_json_source for now."
    )
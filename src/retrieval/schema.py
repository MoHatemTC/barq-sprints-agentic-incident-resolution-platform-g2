"""
Canonical, normalized article representation used throughout the ingestion
pipeline -- regardless of whether the article came from a local JSON file
(testing) or ServiceNow's Table API (final, via S1.5's client).
"""

from dataclasses import dataclass


@dataclass
class Article:
    sys_id: str          # ServiceNow internal ID (placeholder until S1.5 hand-off)
    number: str           # ServiceNow KB number, e.g. "KB0010001"
    article_id: str       # our own stable ID used for deterministic point IDs
    title: str
    body: str              # raw article content (HTML or markdown, pre-chunking)
    category: str
    service: str
    workflow_state: str   # draft | published | retired
    version: int
    security_level: str
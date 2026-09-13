"""Canonical article representation shared by retrieval sources."""

from dataclasses import dataclass


@dataclass
class Article:
    sys_id: str
    number: str
    article_id: str
    title: str
    body: str
    category: str
    service: str
    workflow_state: str
    version: int
    security_level: str

"""
Turn eval/planted_documents.json into Qdrant points.

Same vectors and same payload keys as S1.4's ingest.py, so a decoy sits next
to real chunks and goes through the exact same filter. Used by
tests/test_planted_exclusion.py (in-memory) and eval/ablation.py --plant (live).
"""

import json
from pathlib import Path

from qdrant_client import models

from src.retrieval.embedding import embed_dense, embed_sparse
from src.retrieval.ingest import deterministic_point_id

PLANTED_PATH = Path(__file__).resolve().parent / "planted_documents.json"


def load_planted() -> list[dict]:
    return json.load(open(PLANTED_PATH, encoding="utf-8"))["documents"]


def planted_numbers() -> set[str]:
    """{"KB9001", "KB9002", "KB9003"} -- handy for 'did any decoy leak?' checks."""
    return {d["article_number"] for d in load_planted()}


def planted_points() -> list[models.PointStruct]:
    """One Qdrant point per decoy, shaped exactly like ingest.py's points."""
    points = []
    for d in load_planted():
        points.append(models.PointStruct(
            # same id recipe as ingest.py -> re-planting is idempotent, un-planting is easy
            id=deterministic_point_id(d["article_number"], d["version"], 0),
            vector={
                "dense": embed_dense(d["body"]),
                "sparse": models.SparseVector(**embed_sparse(d["body"])),
            },
            payload={
                "number": d["article_number"],
                "article_id": d["article_number"],
                "title": d["title"],
                "section": "planted",
                "category": d["category"],
                "service": d["service"],
                "workflow_state": d["workflow_state"],   # <- what the filter reads
                "security_level": d["security_level"],   # <- what the filter reads
                "version": d["version"],
                "chunk_index": 0,
                "text": d["body"],
                "_planted": True,
            },
        ))
    return points

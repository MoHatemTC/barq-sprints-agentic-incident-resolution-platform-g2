import logging
import os
from typing import Dict, Any

from src.observability.tracing import trace_node
from src.retrieval.hybrid_search import search
from src.retrieval.filters import RetrievalFilters

logger = logging.getLogger(__name__)
CACHE_HIT_SCORE = float(os.getenv("RETRIEVAL_CACHE_HIT_SCORE", "0.55"))

@trace_node(name="retrieve", observation_type="retriever")
def retrieve_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute hybrid retrieval. Returns an empty list when nothing is found."""
    payload = state.get("incident_payload", {})
    incident_text = payload.get("description") or payload.get("short_description") or ""
    human_solution = state.get("human_solution") or ""
    text = f"{incident_text}\nHuman-provided resolution:\n{human_solution}" if human_solution else incident_text

    if not text.strip():
        logger.warning("Empty incident text; skipping retrieval")
        return {"retrieved_evidence": [], "retrieval_failed": False}

    try:
        category = payload.get("category")
        service = payload.get("business_service") or payload.get("service")

        filters = RetrievalFilters(
            category=category if category else None,
            service=service if service else None,
        )
        chunks = search(query=text, filters=filters)

        if not chunks and filters.category:
            logger.info(f"No results with category={filters.category}; retrying without category filter")
            filters = RetrievalFilters(
                category=None,
                service=service if service else None,
            )
            chunks = search(query=text, filters=filters)

        retrieved = [
            {
                "id": chunk.number or chunk.point_id,
                "text": chunk.text,
                "score": chunk.score,
            }
            for chunk in chunks
        ]

        if not retrieved:
            logger.warning(f"Retrieval returned no results (query='{text[:60]}')")
        else:
            logger.info(f"Retrieved {len(retrieved)} chunks: {[(r['id'], round(r['score'], 3)) for r in retrieved]}")

        result = {"retrieved_evidence": retrieved, "retrieval_failed": False}
        # A strong previous KB match is already an approved resolution. Reuse
        # it directly to avoid repeating diagnose/generate/critic/LLM calls.
        if retrieved and retrieved[0]["score"] >= CACHE_HIT_SCORE:
            result["cached_resolution"] = retrieved[0]["text"]
            result["outputs"] = {
                **(state.get("outputs") or {}),
                "resolution": retrieved[0]["text"],
            }
            result["retrieval_cache_hit"] = True
            logger.info(
                "Strong KB match reused as cached resolution: %s (score=%.3f)",
                retrieved[0]["id"], retrieved[0]["score"],
            )
        else:
            result["retrieval_cache_hit"] = False
        return result

    except Exception as e:
        logger.exception(f"Retrieval failed: {e}")
        return {"retrieved_evidence": [], "retrieval_failed": True}

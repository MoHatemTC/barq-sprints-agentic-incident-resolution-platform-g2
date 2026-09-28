import logging
import os
from typing import Dict, Any

from src.observability.tracing import trace_node
from src.retrieval.hybrid_search import search
from src.retrieval.filters import RetrievalFilters

logger = logging.getLogger(__name__)
# Only human-approved resolutions (KBHR- articles from knowledge capture) are reused.
# Manual sections and seeded KBs score high on unanswerable tickets too, so they
# always go through diagnose/generate. The score is the cross-encoder logit (hybrid_rerank).
CACHE_HIT_PREFIX = "KBHR-"
CACHE_HIT_SCORE = float(os.getenv("RETRIEVAL_CACHE_HIT_SCORE", "5.0"))


def _cached_resolution(text: str) -> str:
    """The steps of a KBHR article, without its incident context."""
    _, marker, steps = text.partition("Resolution:")
    return steps.strip() if marker and steps.strip() else text

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
        # A strong match on a human-approved article is an existing resolution.
        # Reuse it directly to avoid repeating diagnose/generate/critic/LLM calls.
        top = retrieved[0] if retrieved else None
        if (
            top
            and str(top["id"]).startswith(CACHE_HIT_PREFIX)
            and top["score"] >= CACHE_HIT_SCORE
        ):
            cached = _cached_resolution(top["text"])
            result["cached_resolution"] = cached
            result["outputs"] = {
                **(state.get("outputs") or {}),
                "resolution": cached,
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

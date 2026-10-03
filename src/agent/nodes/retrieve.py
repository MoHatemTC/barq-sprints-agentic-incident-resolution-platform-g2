import logging
import os
import re
from typing import Dict, Any

from src.agent.llm import get_llm
from src.config import INCIDENT_CATEGORIES, RETRIEVAL
from src.observability.tracing import get_llm_callback, trace_node
from src.retrieval.hybrid_search import article_key, article_texts, best_per_article, search
from src.retrieval.filters import RetrievalFilters

logger = logging.getLogger(__name__)
# Only human-approved resolutions (KBHR- articles from knowledge capture) are reused.
# Manual sections and seeded KBs score high on unanswerable tickets too, so they
# always go through diagnose/generate. The score is the cross-encoder logit (hybrid_rerank).
CACHE_HIT_PREFIX = "KBHR-"
CACHE_HIT_SCORE = float(os.getenv("RETRIEVAL_CACHE_HIT_SCORE", "0.90"))
# Below this best score (cross-encoder probability) the incident's category is treated as wrong
# and the agent searches the FALLBACK_CATEGORIES categories it finds most likely instead.
FALLBACK_MIN_SCORE = float(os.getenv("RETRIEVAL_FALLBACK_MIN_SCORE", "0.35"))
FALLBACK_CATEGORIES = int(os.getenv("RETRIEVAL_FALLBACK_CATEGORIES", "3"))


def _incident_text(payload: Dict[str, Any]) -> str:
    """Short description + description, for when formulate_query did not run."""
    texts = ((payload.get("short_description") or "").strip(), (payload.get("description") or "").strip())
    return "\n".join(dict.fromkeys(t for t in texts if t))


def _with_full_articles(chunks: list) -> list:
    """Evidence is the whole article, not one chunk of it: a Symptom chunk alone has no steps."""
    ids = [article_key(c) for c in chunks if "chunk_index" in c.payload]
    if not ids:
        return chunks
    try:
        texts = article_texts(ids)
    except Exception as exc:
        logger.warning("Full article text not loaded (%s); using the matched chunks", exc)
        return chunks
    for c in chunks:
        c.text = texts.get(article_key(c), c.text)
    return chunks


def _likely_categories(text: str, wrong: str) -> list[str]:
    """The most likely ServiceNow categories for the incident, best first, excluding `wrong`."""
    options = [c for c in INCIDENT_CATEGORIES if c != wrong]
    prompt = (
        f"An IT incident was filed under the category '{wrong}', but nothing in that category matches it.\n"
        f"Pick the {FALLBACK_CATEGORIES} most likely categories from: {', '.join(options)}.\n"
        f"Answer with the category names only, best first, comma-separated.\n\nIncident:\n{text}"
    )
    reply = get_llm().invoke(prompt, config={"callbacks": get_llm_callback()})
    words = re.findall(r"[a-z_]+", str(getattr(reply, "content", reply)).lower())
    return list(dict.fromkeys(w for w in words if w in options))[:FALLBACK_CATEGORIES]


def _search_by_category(text: str, category: str | None) -> list:
    """Search the incident's category; if it has no good match, the agent's likeliest categories."""
    chunks = search(query=text, filters=RetrievalFilters(category=category))
    if not category or (chunks and chunks[0].score >= FALLBACK_MIN_SCORE):
        return chunks

    likely = _likely_categories(text, category)
    if not likely:
        return chunks
    corrected = search(query=text, filters=RetrievalFilters(categories=tuple(likely)))
    logger.info(
        "No good match in category=%s (best=%s); searched %s instead: %s",
        category, round(chunks[0].score, 3) if chunks else None, likely,
        [(c.number, c.payload.get("category"), round(c.score, 3)) for c in corrected],
    )
    return best_per_article(sorted(chunks + corrected, key=lambda c: -c.score))[:RETRIEVAL.top_k]


def _cached_resolution(text: str) -> str:
    """The steps of a KBHR article, without its incident context."""
    _, marker, steps = text.partition("Resolution:")
    return steps.strip() if marker and steps.strip() else text

@trace_node(name="retrieve", observation_type="retriever")
def retrieve_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute hybrid retrieval. Returns an empty list when nothing is found."""
    payload = state.get("incident_payload", {})
    human_solution = state.get("human_solution") or ""

    # The query formulate_query wrote; the raw incident text if it did not run
    text = state.get("search_query") or _incident_text(payload)
    if human_solution:
        text = f"{text}\nHuman-provided resolution:\n{human_solution}"

    if not text.strip():
        logger.warning("Empty incident text; skipping retrieval")
        return {"retrieved_evidence": [], "retrieval_failed": False}

    try:
        # The category chosen on the incident. Service is not filtered on: incidents carry
        # business_service as a sys_id, which never equals an article's service name.
        chunks = _with_full_articles(_search_by_category(text, payload.get("category") or None))

        retrieved = [
            {
                "id": chunk.number or chunk.point_id,
                "text": chunk.text,
                "score": chunk.score,
                "category": chunk.payload.get("category", ""),
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

"""
Retrieval smoke test for S1.4.

Proves the vector store isn't just populated -- it actually returns the
correct article for a real query. Uses dense-only search for now (simplest
path to a working result); hybrid dense+sparse fusion is Sprint 2/3 scope.

Usage:
    python -m src.retrieval.smoke_test
    python -m src.retrieval.smoke_test "custom query text"
"""

import os
import sys
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from .embedding import embed_dense

load_dotenv()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "barq_knowledge_base")

# A handful of real, known-answer queries pulled from coverage_matrix.csv --
# each one should surface its expected article near the top of results.
KNOWN_QUERIES = [
    {
        "query": "VPN authentication keeps failing after I changed my password",
        "expected_article_number": "KB0001",
    },
    {
        "query": "Outlook shows disconnected and no email is coming through",
        "expected_article_number": "KB0002",
    },
    {
        "query": "My account got locked after too many failed login attempts",
        "expected_article_number": "KB0005",
    },
    {
        "query": "requesting annual leave for next month",  # deliberately unanswerable
        "expected_article_number": None,
    },
]


def search(query: str, top_k: int = 3):
    client = QdrantClient(url=QDRANT_URL, check_compatibility=False)
    vector = embed_dense(query)

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        using="dense",
        limit=top_k,
        with_payload=True,
    ).points

    # filter out the model-fingerprint marker point if it ever surfaces
    return [r for r in results if not r.payload.get("_is_marker")]


def run_smoke_test():
    print(f"Running retrieval smoke test against collection '{COLLECTION_NAME}'\n")
    passed = 0
    failed = 0

    for case in KNOWN_QUERIES:
        query = case["query"]
        expected = case["expected_article_number"]
        results = search(query)

        top_article_numbers = [r.payload.get("number") for r in results]
        top_score = results[0].score if results else None

        print(f"Query: {query!r}")
        print(f"  Expected article: {expected or '(none -- unanswerable)'}")
        print(f"  Top {len(results)} results:")
        for r in results:
            print(f"    - {r.payload.get('number')} [{r.payload.get('section')}] "
                  f"score={r.score:.3f} :: {r.payload.get('title')}")

        if expected is None:
            # Unanswerable queries don't "pass/fail" retrieval itself -- dense
            # search will always return *something* topically closest. What
            # matters is recording the top score as real evidence for where
            # Sprint 3's confidence threshold should sit.
            ok = True
            verdict = (f"OBSERVED (top score {top_score:.3f} -- record for "
                       f"Sprint 3 confidence threshold decision)") if top_score else \
                      "OBSERVED (no results returned)"
        else:
            ok = expected in top_article_numbers
            verdict = "PASS" if ok else f"FAIL (expected {expected} not in top results)"

        print(f"  Verdict: {verdict}\n")
        passed += int(ok)
        failed += int(not ok)

    print(f"Smoke test complete: {passed} passed, {failed} failed out of {len(KNOWN_QUERIES)}")
    return failed == 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        custom_query = " ".join(sys.argv[1:])
        print(f"Custom query: {custom_query!r}\n")
        for r in search(custom_query):
            print(f"  - {r.payload.get('number')} [{r.payload.get('section')}] "
                  f"score={r.score:.3f} :: {r.payload.get('title')}")
    else:
        success = run_smoke_test()
        sys.exit(0 if success else 1)
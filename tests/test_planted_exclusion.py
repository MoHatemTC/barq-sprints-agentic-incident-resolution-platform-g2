# The pre-filter proof. In-memory Qdrant: 3 real published chunks + the 3 planted decoys.
# Each decoy copies a real article's text, so WITHOUT the filter it ranks #1.
# Run: pytest tests/test_planted_exclusion.py -v
import pytest
from qdrant_client import QdrantClient, models

from eval.planting import load_planted, planted_numbers, planted_points
from src.retrieval.embedding import embed_dense, embed_sparse, get_dense_dimension
from src.retrieval.filters import RetrievalFilters
from src.retrieval.hybrid_search import search

COLLECTION = "test_planted"
MODES = ["dense", "hybrid", "hybrid_rerank"]
DECOYS = load_planted()

# Real competition for the decoys.
REAL = [
    ("KB0001", "The VPN client caches the previous credential. Sign out and enter the new password."),
    ("KB0005", "Lockout triggers after 5 failed attempts. Verify identity, then unlock the account."),
    ("KB0022", "Replace the saved SAP Logon entry that targets APPSRV-OLD-04 with the message server group."),
]

# Filters switched OFF -- used only to prove the decoys CAN come back.
NO_FILTER = RetrievalFilters(allowed_workflow_states=("published", "retired", "draft"),
                             blocked_security_levels=())


@pytest.fixture(scope="module")
def client():
    c = QdrantClient(":memory:")
    c.create_collection(
        COLLECTION,
        vectors_config={"dense": models.VectorParams(size=get_dense_dimension(), distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    real = [
        models.PointStruct(
            id=i + 1,
            vector={"dense": embed_dense(text), "sparse": models.SparseVector(**embed_sparse(text))},
            payload={"number": number, "section": "Resolution", "workflow_state": "published",
                     "security_level": "internal", "text": text, "title": text[:40]},
        )
        for i, (number, text) in enumerate(REAL)
    ]
    c.upsert(COLLECTION, real + planted_points())
    return c


def numbers(client, query, mode, filters=None):
    """KB numbers returned, in rank order. top_k=50 = 'give me everything'."""
    return [h.number for h in search(query, mode=mode, top_k=50, filters=filters,
                                     client=client, collection=COLLECTION)]


# ---- half A: with the default filter, a decoy is never returned (3 decoys x 3 modes)

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("doc", DECOYS, ids=lambda d: d["article_number"])
def test_planted_document_is_never_returned(client, mode, doc):
    got = numbers(client, doc["probe_query"], mode)
    assert not set(got) & planted_numbers()


# ---- half B: with the filter off, the same decoy is rank 1 (so half A has teeth)

@pytest.mark.parametrize("doc", DECOYS, ids=lambda d: d["article_number"])
def test_planted_document_is_rank_1_when_filter_is_off(client, doc):
    got = numbers(client, doc["probe_query"], "dense", filters=NO_FILTER)
    assert got[0] == doc["article_number"]


# ---- KB9003 is published but restricted: only the security rule can stop it

def test_restricted_decoy_is_blocked_by_security_rule_alone(client):
    state_rule_only = RetrievalFilters(blocked_security_levels=())
    query = DECOYS[2]["probe_query"]
    assert "KB9003" in numbers(client, query, "dense", filters=state_rule_only)
    assert "KB9003" not in numbers(client, query, "dense")

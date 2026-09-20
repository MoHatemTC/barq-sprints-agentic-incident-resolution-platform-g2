# Integration tests for real embeddings + an in-memory Qdrant to run it use  "pytest tests/test_hybrid_search.py -v"
import pytest
from qdrant_client import QdrantClient, models

from src.retrieval.embedding import embed_dense, embed_sparse, get_dense_dimension
from src.retrieval.hybrid_search import RetrievedChunk, rrf_fuse, search

COLLECTION = "test_kb"

# (number, section, workflow_state, text) , same payload keys S1.4's ingest.py writes.
DOCS = [
    ("KB0001", "Resolution", "published", "VPN authentication fails after a password change. Clear the cached credentials and sign in again."),
    ("KB0004", "Resolution", "published", "Print jobs queue but nothing prints. Restart the print spooler service on the print server."),
    ("KB0022", "Cause", "published", "SAP GUI still points to APPSRV-OLD-04 after the 2025 migration. Update the saved connection entry."),
    ("KB0011", "Resolution", "draft", "Zoom audio not working in meetings. Select the correct microphone in Zoom settings."),
]


@pytest.fixture(scope="module")
def client():
    """Build the mini collection once for the whole file (embedding is the slow part)"""
    c = QdrantClient(":memory:")
    c.create_collection(
        COLLECTION,
        vectors_config={"dense": models.VectorParams(size=get_dense_dimension(), distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    points = [
        models.PointStruct(
            id=i + 1,
            vector={"dense": embed_dense(text), "sparse": models.SparseVector(**embed_sparse(text))},
            payload={"number": number, "section": section, "workflow_state": state,
                     "security_level": "internal", "text": text, "title": text[:40]},
        )
        for i, (number, section, state, text) in enumerate(DOCS)
    ]
    c.upsert(COLLECTION, points)
    return c


def run(client, query, mode, top_k=3):
    return search(query, mode=mode, top_k=top_k, client=client, collection=COLLECTION)


# testing all modes

@pytest.mark.parametrize("mode", ["dense", "hybrid", "hybrid_rerank"])
def test_returns_at_most_top_k_chunks(client, mode):
    results = run(client, "vpn fails after password change", mode, top_k=2)
    assert len(results) <= 2
    assert all(isinstance(r, RetrievedChunk) for r in results)


@pytest.mark.parametrize("mode", ["dense", "hybrid", "hybrid_rerank"])
def test_results_sorted_best_first(client, mode):
    scores = [r.score for r in run(client, "printer prints nothing", mode)]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("mode", ["dense", "hybrid", "hybrid_rerank"])
def test_same_query_twice_gives_same_order(client, mode):
    first = [r.point_id for r in run(client, "sap gui connection", mode)]
    second = [r.point_id for r in run(client, "sap gui connection", mode)]
    assert first == second


@pytest.mark.parametrize("mode", ["dense", "hybrid", "hybrid_rerank"])
def test_draft_chunk_is_never_returned(client, mode):
    # The query is literally the draft article's text -- it would rank #1 without the filter.
    results = run(client, "Zoom audio not working in meetings", mode, top_k=10)
    assert "KB0011" not in [r.number for r in results]


def test_exact_identifier_is_found_by_hybrid(client):
    # A bare identifier has almost no "meaning" for dense; sparse matches the token exactly.
    results = run(client, "APPSRV-OLD-04", "hybrid", top_k=1)
    assert results[0].number == "KB0022"


# testing rrf_fuse (pure)

def chunk(pid):
    return RetrievedChunk(point_id=pid, number="", section="", text="", score=0.0, payload={})


def test_rrf_fuse_worked_example():
    dense = [chunk("A"), chunk("B"), chunk("C")]
    sparse = [chunk("C"), chunk("D"), chunk("A")]
    fused = rrf_fuse([dense, sparse], k=60)

    ids = [c.point_id for c in fused]
    assert ids == ["A", "C", "B", "D"]                 # A and C are in both lists -> top
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 63)
    assert fused[2].score == pytest.approx(1 / 62)      # B: one list only


def test_rrf_fuse_ties_break_on_point_id():
    # Same rank in one list each -> equal score -> smaller id first, every time.
    fused = rrf_fuse([[chunk("Z")], [chunk("A")]], k=60)
    assert [c.point_id for c in fused] == ["A", "Z"]

from types import SimpleNamespace

from qdrant_client import QdrantClient, models

from src.agent import knowledge_capture
from src.config import QDRANT
from src.retrieval import ingest
from src.retrieval.embedding import get_dense_dimension
from src.retrieval.hybrid_search import search


COLLECTION = QDRANT.collection_name


def test_full_human_resolution_loop_closes(monkeypatch):
    """
    S3.5 loop-closure test:

        incident
          -> human resolution
          -> Article Composer
          -> ServiceNow publish + verification
          -> Qdrant ingestion
          -> deterministic point ID
          -> equivalent incident retrieval
          -> newly created article returned
    """

    execution_id = "exec-loop-001"
    article_number = "KB-LOOP-001"

    incident_snapshot = {
        "number": "INC-LOOP-001",
        "short_description": "Email service unavailable",
        "description": "Users cannot access the email service.",
    }

    human_solution = (
        "Restart the email service and verify that users can access email again."
    )

    # ---------------------------------------------------------
    # 1. Local in-memory Qdrant
    # ---------------------------------------------------------

    client = QdrantClient(":memory:")

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": models.VectorParams(
                size=get_dense_dimension(),
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )

    # Make the production ingestion code use our in-memory Qdrant.
    monkeypatch.setattr(
        ingest,
        "QdrantClient",
        lambda **kwargs: client,
    )

    # Collection already exists in this test.
    monkeypatch.setattr(
        ingest,
        "_ensure_collection",
        lambda client: None,
    )

    # ---------------------------------------------------------
    # 2. Article Composer
    # ---------------------------------------------------------

    composed_article = {
        "title": "Email Service Recovery",
        "summary": "Users could not access the email service.",
        "steps": [
            "Restart the email service.",
            "Verify that users can access email again.",
        ],
    }

    monkeypatch.setattr(
        knowledge_capture,
        "compose_article",
        lambda incident_snapshot, human_solution: composed_article,
    )

    # ---------------------------------------------------------
    # 3. ServiceNow publish + read-back verification
    # ---------------------------------------------------------

    published_article = {
        "status": "created",
        "article_number": article_number,
        "sys_id": "snow-loop-001",
    }

    monkeypatch.setattr(
        knowledge_capture,
        "publish_article",
        lambda article: published_article,
    )

    # ---------------------------------------------------------
    # 4. Simulated ServiceNow article returned to ingestion
    # ---------------------------------------------------------

    article = SimpleNamespace(
        article_id=article_number,
        number=article_number,
        sys_id="snow-loop-001",
        title=composed_article["title"],
        body=(
            "Incident context:\n"
            "Users could not access the email service.\n\n"
            "Resolution:\n"
            "1. Restart the email service.\n"
            "2. Verify that users can access email again."
        ),
        category="service",
        service="email",
        workflow_state="published",
        version=1,
        security_level="internal",
    )

    monkeypatch.setattr(
        "src.retrieval.sources.servicenow_source."
        "load_articles_from_servicenow",
        lambda: [article],
    )

    # Qdrant starts empty.
    monkeypatch.setattr(
        ingest,
        "_stored_hashes",
        lambda client: {},
    )

    monkeypatch.setattr(
        ingest,
        "_delete_article",
        lambda client, article_id: None,
    )

    # ---------------------------------------------------------
    # 5. Execute the real knowledge-capture orchestration
    # ---------------------------------------------------------

    result = knowledge_capture.capture_human_resolution(
        incident_snapshot=incident_snapshot,
        human_solution=human_solution,
        article_number=article_number,
        category="service",
        service="email",
        security_level="internal",
        execution_identifier=execution_id,
    )

    # ---------------------------------------------------------
    # 6. ServiceNow + Qdrant consistency
    # ---------------------------------------------------------

    assert result["status"] == "completed"
    assert result["consistency"] == "synchronized"

    assert result["servicenow"]["status"] == "created"
    assert result["servicenow"]["sys_id"] == "snow-loop-001"

    assert result["qdrant"]["status"] == "synced"
    assert result["qdrant"]["point_ids"]

    # ---------------------------------------------------------
    # 7. Deterministic point ID
    # ---------------------------------------------------------

    point_ids = result["qdrant"]["point_ids"]

    expected_point_id = ingest.deterministic_point_id(
        article_number,
        1,
        0,
    )

    assert expected_point_id == point_ids[0]

    # ---------------------------------------------------------
    # 8. Equivalent incident retrieval
    # ---------------------------------------------------------

    query = (
        "Users cannot access email service. "
        "Restart the email service."
    )

    results = search(
        query,
        mode="hybrid",
        top_k=5,
        client=client,
        collection=COLLECTION,
    )

    assert results

    returned_numbers = [
        item.number
        for item in results
    ]

    assert article_number in returned_numbers

    matching = [
        item
        for item in results
        if item.number == article_number
    ]

    assert matching

    retrieved_article = matching[0]

    assert retrieved_article.payload["workflow_state"] == "published"
    assert retrieved_article.payload["security_level"] == "internal"
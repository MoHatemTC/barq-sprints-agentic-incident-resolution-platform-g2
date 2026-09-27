from types import SimpleNamespace

from src.retrieval import ingest


def test_deterministic_point_id_is_stable():
    first = ingest.deterministic_point_id(
        "KB001",
        1,
        0,
    )

    second = ingest.deterministic_point_id(
        "KB001",
        1,
        0,
    )

    assert first == second


def test_deterministic_point_id_changes_for_different_chunks():
    first = ingest.deterministic_point_id(
        "KB001",
        1,
        0,
    )

    second = ingest.deterministic_point_id(
        "KB001",
        1,
        1,
    )

    assert first != second


def test_sync_kb_returns_qdrant_point_ids(monkeypatch):
    article = SimpleNamespace(
        article_id="KB001",
        number="KB001",
        sys_id="sn-001",
        title="Restart service",
        body="Restart the service.",
        category="network",
        service="Email",
        workflow_state="published",
        version=1,
        security_level="internal",
    )

    class FakeClient:
        def upsert(self, collection_name, points):
            self.points = points

    client = FakeClient()

    monkeypatch.setattr(
        ingest,
        "QdrantClient",
        lambda **kwargs: client,
    )

    monkeypatch.setattr(
        ingest,
        "_ensure_collection",
        lambda client: None,
    )

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

    # sync_kb() imports load_articles_from_servicenow
    # from this module inside the function, so patch it
    # where it is actually defined.
    monkeypatch.setattr(
        "src.retrieval.sources.servicenow_source.load_articles_from_servicenow",
        lambda: [article],
    )

    monkeypatch.setattr(
        ingest,
        "_build_points",
        lambda articles: [
            SimpleNamespace(
                id="point-001",
                payload={},
            ),
            SimpleNamespace(
                id="point-002",
                payload={},
            ),
        ],
    )

    result = ingest.sync_kb()

    assert result["status"] == "synced"
    assert result["added"] == 1
    assert result["point_ids"] == [
        "point-001",
        "point-002",
    ]
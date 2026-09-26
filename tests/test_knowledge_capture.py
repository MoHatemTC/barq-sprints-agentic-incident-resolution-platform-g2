from src.agent import knowledge_capture


def test_capture_human_resolution_completes(monkeypatch):
    composed_article = {
        "title": "Restart the affected service",
        "summary": "The service was unavailable.",
        "steps": [
            "Restart the affected service.",
            "Verify that the incident is resolved.",
        ],
    }

    fake_article = object()

    monkeypatch.setattr(
        knowledge_capture,
        "compose_article",
        lambda incident_snapshot, human_solution: composed_article,
    )

    monkeypatch.setattr(
        knowledge_capture,
        "composer_result_to_article",
        lambda **kwargs: fake_article,
    )

    monkeypatch.setattr(
        knowledge_capture,
        "publish_article",
        lambda article: {
            "status": "created",
            "article_number": "KB0100",
            "sys_id": "sn-123",
        },
    )

    monkeypatch.setattr(
        knowledge_capture,
        "sync_kb",
        lambda: {
            "status": "synced",
            "added": 1,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
            "point_ids": [
                "point-001",
                "point-002",
            ],
        },
    )

    result = knowledge_capture.capture_human_resolution(
        incident_snapshot={
            "number": "INC001100",
            "short_description": "Service unavailable",
        },
        human_solution="Restart the affected service.",
        article_number="KB0100",
        category="network",
        service="network",
    )

    assert result["status"] == "completed"
    assert result["consistency"] == "synchronized"

    assert result["servicenow"]["status"] == "created"
    assert result["servicenow"]["sys_id"] == "sn-123"

    assert result["qdrant"]["status"] == "synced"
    assert result["qdrant"]["added"] == 1
    assert result["qdrant"]["point_ids"] == [
        "point-001",
        "point-002",
    ]

    assert result["article"] == composed_article
    assert result["article_number"] == "KB0100"


def test_capture_reports_partial_success_when_qdrant_fails(
    monkeypatch,
):
    composed_article = {
        "title": "Restart the affected service",
        "summary": "The service was unavailable.",
        "steps": [
            "Restart the affected service.",
        ],
    }

    monkeypatch.setattr(
        knowledge_capture,
        "compose_article",
        lambda incident_snapshot, human_solution: composed_article,
    )

    monkeypatch.setattr(
        knowledge_capture,
        "composer_result_to_article",
        lambda **kwargs: object(),
    )

    monkeypatch.setattr(
        knowledge_capture,
        "publish_article",
        lambda article: {
            "status": "created",
            "article_number": "KB0101",
            "sys_id": "sn-456",
        },
    )

    monkeypatch.setattr(
        knowledge_capture,
        "sync_kb",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Qdrant unavailable")
        ),
    )

    result = knowledge_capture.capture_human_resolution(
        incident_snapshot={
            "number": "INC001101",
            "short_description": "Service unavailable",
        },
        human_solution="Restart the affected service.",
        article_number="KB0101",
        category="network",
    )

    assert result["status"] == "partial_success"

    assert result["servicenow"]["status"] == "created"
    assert result["servicenow"]["sys_id"] == "sn-456"

    assert result["qdrant"]["status"] == "failed"
    assert result["qdrant"]["error"] == "Qdrant unavailable"

    assert (
        result["consistency"]
        == "servicenow_published_qdrant_sync_failed"
    )


def test_capture_rejects_empty_human_solution():
    try:
        knowledge_capture.capture_human_resolution(
            incident_snapshot={
                "number": "INC001102",
                "short_description": "Service unavailable",
            },
            human_solution="",
            article_number="KB0102",
            category="network",
        )
    except ValueError as exc:
        assert str(exc) == "human_solution is required"
    else:
        raise AssertionError(
            "Expected ValueError for empty human_solution"
        )


def test_capture_records_audit_result(monkeypatch):
    composed_article = {
        "title": "Restart the affected service",
        "summary": "The service was unavailable.",
        "steps": [
            "Restart the affected service.",
        ],
    }

    fake_article = object()
    audit_calls = []

    monkeypatch.setattr(
        knowledge_capture,
        "compose_article",
        lambda incident_snapshot, human_solution: composed_article,
    )

    monkeypatch.setattr(
        knowledge_capture,
        "composer_result_to_article",
        lambda **kwargs: fake_article,
    )

    monkeypatch.setattr(
        knowledge_capture,
        "publish_article",
        lambda article: {
            "status": "created",
            "article_number": "KB0200",
            "sys_id": "sn-789",
        },
    )

    monkeypatch.setattr(
        knowledge_capture,
        "sync_kb",
        lambda: {
            "status": "synced",
            "added": 1,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
            "point_ids": [
                "point-101",
                "point-102",
            ],
        },
    )

    monkeypatch.setattr(
        knowledge_capture,
        "record_knowledge_capture",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    fake_db = object()

    result = knowledge_capture.capture_human_resolution(
        incident_snapshot={
            "number": "INC002000",
            "short_description": "Service unavailable",
        },
        human_solution="Restart the affected service.",
        article_number="KB0200",
        category="network",
        service="network",
        execution_identifier="execution-123",
        db=fake_db,
    )

    assert result["status"] == "completed"

    assert len(audit_calls) == 1

    audit = audit_calls[0]

    assert audit["db"] is fake_db
    assert audit["execution_reference"] == "execution-123"
    assert audit["status"] == "completed"
    assert audit["article_number"] == "KB0200"
    assert audit["article_sys_id"] == "sn-789"
    assert audit["qdrant_point_ids"] == [
        "point-101",
        "point-102",
    ]   
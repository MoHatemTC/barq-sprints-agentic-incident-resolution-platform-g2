import uuid

from src.db.database import SessionLocal
from src.db.models import KnowledgeCaptureAudit
from src.db.knowledge_capture_service import (
    record_knowledge_capture,
    get_knowledge_capture_audits,
)


def test_knowledge_capture_audit_can_be_recorded_and_read():
    execution_identifier = (
        f"knowledge-capture-test-{uuid.uuid4()}"
    )

    db = SessionLocal()

    try:
        audit = record_knowledge_capture(
            db=db,
            execution_reference=execution_identifier,
            status="completed",
            article_number="KB0200",
            article_sys_id="sn-789",
            qdrant_point_ids=[
                "point-101",
                "point-102",
            ],
        )

        assert audit.id is not None
        assert audit.execution_reference == execution_identifier
        assert audit.status == "completed"
        assert audit.article_number == "KB0200"
        assert audit.article_sys_id == "sn-789"
        assert audit.qdrant_point_ids == [
            "point-101",
            "point-102",
        ]
        assert audit.error is None

        records = get_knowledge_capture_audits(
            db=db,
            execution_reference=execution_identifier,
        )

        assert len(records) == 1

        record = records[0]

        assert record.execution_reference == execution_identifier
        assert record.article_number == "KB0200"
        assert record.article_sys_id == "sn-789"
        assert record.qdrant_point_ids == [
            "point-101",
            "point-102",
        ]
        assert record.status == "completed"

    finally:
        db.query(KnowledgeCaptureAudit).filter(
            KnowledgeCaptureAudit.execution_reference
            == execution_identifier
        ).delete(
            synchronize_session=False
        )

        db.commit()
        db.close()
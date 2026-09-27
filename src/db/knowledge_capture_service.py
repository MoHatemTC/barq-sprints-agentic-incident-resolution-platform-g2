from sqlalchemy.orm import Session

from src.db.models import KnowledgeCaptureAudit


def record_knowledge_capture(
    db: Session,
    execution_reference: str,
    status: str,
    article_number: str | None = None,
    article_sys_id: str | None = None,
    qdrant_point_ids: list[str] | None = None,
    error: str | None = None,
) -> KnowledgeCaptureAudit:
    """
    Store the result of a human-resolution knowledge capture.

    The record connects one execution with the resulting
    ServiceNow article and Qdrant point IDs.
    """

    audit = KnowledgeCaptureAudit(
        execution_reference=execution_reference,
        article_number=article_number,
        article_sys_id=article_sys_id,
        qdrant_point_ids=qdrant_point_ids or [],
        status=status,
        error=error,
    )

    db.add(audit)
    db.commit()
    db.refresh(audit)

    return audit


def get_knowledge_capture_audits(
    db: Session,
    execution_reference: str,
) -> list[KnowledgeCaptureAudit]:
    """
    Return all knowledge-capture audit records for an execution.
    """

    return (
        db.query(KnowledgeCaptureAudit)
        .filter(
            KnowledgeCaptureAudit.execution_reference
            == execution_reference
        )
        .order_by(KnowledgeCaptureAudit.id.asc())
        .all()
    )
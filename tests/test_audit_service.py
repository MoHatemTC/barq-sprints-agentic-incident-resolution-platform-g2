import uuid
from datetime import datetime, timezone

from src.db.database import SessionLocal
from src.db.models import (
    Approval,
    Execution,
    Failure,
    KnowledgeCaptureAudit,
    WorkflowState,
)
from src.db.audit_service import get_execution_audit


def cleanup(execution_identifier):
    db = SessionLocal()

    try:
        # Approval records are immutable.
        # They are intentionally not deleted here.

        db.query(KnowledgeCaptureAudit).filter(
            KnowledgeCaptureAudit.execution_reference
            == execution_identifier
        ).delete(
            synchronize_session=False
        )

        db.query(Failure).filter(
            Failure.execution_reference == execution_identifier
        ).delete(
            synchronize_session=False
        )

        db.query(WorkflowState).filter(
            WorkflowState.execution_reference == execution_identifier
        ).delete(
            synchronize_session=False
        )

        db.commit()

    finally:
        db.close()


def test_execution_audit_returns_complete_audit_trail():
    execution_identifier = (
        f"audit-test-execution-{uuid.uuid4()}"
    )

    db = SessionLocal()

    try:
        execution = Execution(
            execution_identifier=execution_identifier,
            incident_reference="INC-AUDIT-001",
            status="failed",
            agent_version="v1",
            model_name="test-model",
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
        )

        db.add(execution)
        db.commit()

        db.add_all(
            [
                WorkflowState(
                    execution_reference=execution_identifier,
                    node_name="detect_incident",
                    checkpoint='{"step":1}',
                ),
                WorkflowState(
                    execution_reference=execution_identifier,
                    node_name="collect_evidence",
                    checkpoint='{"evidence":"logs collected"}',
                ),
            ]
        )

        db.add(
            Failure(
                execution_reference=execution_identifier,
                failing_node="collect_evidence",
                error_class="RuntimeError",
                message="Evidence collection failed",
                retry_count=1,
            )
        )

        db.add(
            Approval(
                execution_reference=execution_identifier,
                evidence_presented="Incident logs",
                reviewer_decision="rejected",
                decision_timestamp=datetime.now(timezone.utc),
                reviewer_identity="test-reviewer",
            )
        )

        db.commit()

        audit = get_execution_audit(
            db,
            execution_identifier,
        )

        assert audit is not None

        assert audit["execution"]["execution_identifier"] == (
            execution_identifier
        )

        assert audit["execution"]["status"] == "failed"

        assert audit["execution"]["termination_reason"] == (
            "Evidence collection failed"
        )

        assert len(audit["nodes"]) == 2

        assert audit["nodes"][0]["sequence"] == 1
        assert audit["nodes"][0]["node_name"] == "detect_incident"

        assert audit["nodes"][1]["sequence"] == 2
        assert audit["nodes"][1]["node_name"] == "collect_evidence"

        assert len(audit["failures"]) == 1

        assert audit["failures"][0]["failing_node"] == (
            "collect_evidence"
        )

        assert len(audit["approvals"]) == 1

        assert audit["approvals"][0]["reviewer_decision"] == (
            "rejected"
        )

        assert audit["approvals"][0]["reviewer_identity"] == (
            "test-reviewer"
        )

        assert audit["knowledge_captures"] == []

    finally:
        db.close()
        cleanup(execution_identifier)


def test_execution_audit_includes_knowledge_capture():
    execution_identifier = (
        f"audit-kb-test-{uuid.uuid4()}"
    )

    db = SessionLocal()

    try:
        execution = Execution(
            execution_identifier=execution_identifier,
            incident_reference="INC-KB-001",
            status="succeeded",
            agent_version="v1",
            model_name="test-model",
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
        )

        db.add(execution)
        db.commit()

        db.add(
            KnowledgeCaptureAudit(
                execution_reference=execution_identifier,
                article_number="KB0200",
                article_sys_id="sn-789",
                qdrant_point_ids=[
                    "point-101",
                    "point-102",
                ],
                status="completed",
                error=None,
            )
        )

        db.commit()

        audit = get_execution_audit(
            db,
            execution_identifier,
        )

        assert audit is not None

        assert len(audit["knowledge_captures"]) == 1

        knowledge_capture = audit["knowledge_captures"][0]

        assert knowledge_capture["article_number"] == "KB0200"
        assert knowledge_capture["article_sys_id"] == "sn-789"
        assert knowledge_capture["qdrant_point_ids"] == [
            "point-101",
            "point-102",
        ]
        assert knowledge_capture["status"] == "completed"
        assert knowledge_capture["error"] is None

    finally:
        db.close()
        cleanup(execution_identifier)
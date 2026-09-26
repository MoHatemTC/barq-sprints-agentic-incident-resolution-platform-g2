from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    DDL,
    event,
    JSON,
)

from src.db.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)

    event_identifier = Column(
        String(255),
        nullable=False,
        index=True
    )

    incident_sys_id = Column(
        String(255),
        nullable=False,
        index=True
    )

    incident_number = Column(
        String(100),
        nullable=False
    )

    event_type = Column(
        String(100),
        nullable=False
    )

    contract_version = Column(
        String(50),
        nullable=False
    )

    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id = Column(Integer, primary_key=True)

    event_identifier = Column(
        String(255),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint(
            "event_identifier",
            name="uq_event_identifier"
        ),
    )


class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True)

    execution_identifier = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True
    )

    incident_reference = Column(
        String(255),
        nullable=False
    )

    status = Column(
        String(50),
        nullable=False,
        index=True
    )

    node_reached = Column(
        String(255),
        nullable=True
    )

    model_name = Column(
        String(255),
        nullable=True
    )

    agent_version = Column(
        String(100),
        nullable=True
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    ended_at = Column(
        DateTime(timezone=True),
        nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('started', 'succeeded', 'failed', 'blocked', 'abandoned')",
            name="ck_executions_status",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_executions_time_order",
        ),
    )


class KnowledgeCaptureAudit(Base):
    __tablename__ = "knowledge_capture_audit"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        nullable=False,
        index=True,
    )

    article_number = Column(
        String(255),
        nullable=True,
    )

    article_sys_id = Column(
        String(255),
        nullable=True,
    )

    qdrant_point_ids = Column(
        JSON,
        nullable=False,
        default=list,
    )

    status = Column(
        String(50),
        nullable=False,
    )

    error = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class WorkflowState(Base):
    __tablename__ = "workflow_state"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        ForeignKey(
            "executions.execution_identifier",
            name="fk_workflow_state_execution",
        ),
        nullable=False,
        index=True
    )

    node_name = Column(
        String(255),
        nullable=False
    )

    checkpoint = Column(
        Text,
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        ForeignKey(
            "executions.execution_identifier",
            name="fk_approvals_execution",
        ),
        nullable=False,
        index=True
    )

    evidence_presented = Column(
        Text,
        nullable=False
    )

    human_solution = Column(
        Text,
        nullable=True
    )

    # Single-use approval grant for HIGH_RISK tools.
    # It is consumed atomically by permissions.is_approved().
    consumed = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    reviewer_decision = Column(
        String(50),
        nullable=False
    )

    decision_timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    reviewer_identity = Column(
        String(255),
        nullable=False
    )


class Failure(Base):
    __tablename__ = "failures"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        ForeignKey(
            "executions.execution_identifier",
            name="fk_failures_execution",
        ),
        nullable=False,
        index=True
    )

    failing_node = Column(
        String(255),
        nullable=False
    )

    error_class = Column(
        String(255),
        nullable=False
    )

    message = Column(
        Text,
        nullable=False
    )

    retry_count = Column(
        Integer,
        nullable=False,
        default=0
    )


class RetryState(Base):
    __tablename__ = "retry_state"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        ForeignKey(
            "executions.execution_identifier",
            name="fk_retry_state_execution",
        ),
        nullable=False,
        index=True
    )

    attempt_count = Column(
        Integer,
        nullable=False,
        default=0
    )

    last_error = Column(
        Text,
        nullable=True
    )

    next_attempt_time = Column(
        DateTime(timezone=True),
        nullable=True
    )


# ==========================================
# Database Triggers & DDL
# ==========================================

approval_trigger_ddl = DDL("""
CREATE OR REPLACE FUNCTION prevent_approval_modification() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'approval records are immutable';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF
            NEW.id IS DISTINCT FROM OLD.id
            OR NEW.execution_reference IS DISTINCT FROM OLD.execution_reference
            OR NEW.evidence_presented IS DISTINCT FROM OLD.evidence_presented
            OR NEW.human_solution IS DISTINCT FROM OLD.human_solution
            OR NEW.reviewer_decision IS DISTINCT FROM OLD.reviewer_decision
            OR NEW.decision_timestamp IS DISTINCT FROM OLD.decision_timestamp
            OR NEW.reviewer_identity IS DISTINCT FROM OLD.reviewer_identity
            OR (
                OLD.consumed = true
                AND NEW.consumed IS DISTINCT FROM OLD.consumed
            )
            OR (
                OLD.consumed = false
                AND NEW.consumed = false
            )
        THEN
            RAISE EXCEPTION 'approval records are immutable except for one-time consumption';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approval_immutable ON approvals;

CREATE TRIGGER approval_immutable
BEFORE UPDATE OR DELETE ON approvals
FOR EACH ROW EXECUTE FUNCTION prevent_approval_modification();
""")

event.listen(Approval.__table__, "after_create", approval_trigger_ddl)
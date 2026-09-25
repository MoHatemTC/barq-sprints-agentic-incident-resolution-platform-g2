from datetime import datetime, timezone

from sqlalchemy import (
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
            "('started', 'succeeded', 'failed', 'blocked', 'abandoned', "
            "'awaiting_approval')",
            name="ck_executions_status",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_executions_time_order",
        ),
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

    reviewer_decision = Column(
        String(50),
        nullable=False
    )

    decision_timestamp = Column(
        DateTime(timezone=True),
        nullable=False
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

approval_trigger_function_ddl = DDL("""
CREATE OR REPLACE FUNCTION prevent_approval_modification() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'approval records are immutable';
END;
$$ LANGUAGE plpgsql;
""")

drop_approval_trigger_ddl = DDL("""
DROP TRIGGER IF EXISTS approval_immutable ON approvals;
""")

approval_trigger_ddl = DDL("""
CREATE TRIGGER approval_immutable
BEFORE UPDATE OR DELETE ON approvals
FOR EACH ROW EXECUTE FUNCTION prevent_approval_modification();
""")

# Tell SQLAlchemy to run this SQL immediately after creating the 'approvals' table
event.listen(Approval.__table__, 'after_create', approval_trigger_function_ddl)
event.listen(Approval.__table__, 'after_create', drop_approval_trigger_ddl)
event.listen(Approval.__table__, 'after_create', approval_trigger_ddl)

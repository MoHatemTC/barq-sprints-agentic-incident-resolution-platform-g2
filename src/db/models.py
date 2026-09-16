from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
        default=datetime.utcnow
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
        default=datetime.utcnow
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
        default=datetime.utcnow
    )

    ended_at = Column(
        DateTime(timezone=True),
        nullable=True
    )
class WorkflowState(Base):
    __tablename__ = "workflow_state"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
        nullable=False,
        index=True
    )

    checkpoint = Column(
        Text,
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

class Approval(Base):
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)

    execution_reference = Column(
        String(255),
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


"""create sprint2 state schema

Revision ID: 416136e54c32
Revises:
Create Date: 2026-09-16

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "416136e54c32"
down_revision: Union[str, Sequence[str], None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_identifier",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "incident_sys_id",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "incident_number",
            sa.String(length=100),
            nullable=False
        ),
        sa.Column(
            "event_type",
            sa.String(length=100),
            nullable=False
        ),
        sa.Column(
            "contract_version",
            sa.String(length=50),
            nullable=False
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False
        ),
    )

    op.create_index(
        "ix_events_event_identifier",
        "events",
        ["event_identifier"]
    )

    op.create_index(
        "ix_events_incident_sys_id",
        "events",
        ["incident_sys_id"]
    )


    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_identifier",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False
        ),
        sa.UniqueConstraint(
            "event_identifier",
            name="uq_event_identifier"
        ),
    )


    op.create_table(
        "executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_identifier",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "incident_reference",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False
        ),
        sa.Column(
            "node_reached",
            sa.String(length=255),
            nullable=True
        ),
        sa.Column(
            "model_name",
            sa.String(length=255),
            nullable=True
        ),
        sa.Column(
            "agent_version",
            sa.String(length=100),
            nullable=True
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False
        ),
        sa.Column(
            "ended_at",
            sa.DateTime(timezone=True),
            nullable=True
        ),
        sa.UniqueConstraint(
            "execution_identifier",
            name="uq_execution_identifier"
        ),
    )

    op.create_index(
        "ix_executions_execution_identifier",
        "executions",
        ["execution_identifier"]
    )

    op.create_index(
        "ix_executions_status",
        "executions",
        ["status"]
    )


    op.create_table(
        "workflow_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_reference",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "checkpoint",
            sa.Text(),
            nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False
        ),
    )

    op.create_index(
        "ix_workflow_state_execution_reference",
        "workflow_state",
        ["execution_reference"]
    )


    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_reference",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "evidence_presented",
            sa.Text(),
            nullable=False
        ),
        sa.Column(
            "reviewer_decision",
            sa.String(length=50),
            nullable=False
        ),
        sa.Column(
            "decision_timestamp",
            sa.DateTime(timezone=True),
            nullable=False
        ),
        sa.Column(
            "reviewer_identity",
            sa.String(length=255),
            nullable=False
        ),
    )

    op.create_index(
        "ix_approvals_execution_reference",
        "approvals",
        ["execution_reference"]
    )


    op.create_table(
        "failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_reference",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "failing_node",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "error_class",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False
        ),
    )

    op.create_index(
        "ix_failures_execution_reference",
        "failures",
        ["execution_reference"]
    )


    op.create_table(
        "retry_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_reference",
            sa.String(length=255),
            nullable=False
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False
        ),
        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True
        ),
        sa.Column(
            "next_attempt_time",
            sa.DateTime(timezone=True),
            nullable=True
        ),
    )

    op.create_index(
        "ix_retry_state_execution_reference",
        "retry_state",
        ["execution_reference"]
    )


def downgrade() -> None:

    op.drop_table("retry_state")
    op.drop_table("failures")
    op.drop_table("approvals")
    op.drop_table("workflow_state")
    op.drop_table("executions")
    op.drop_table("idempotency_keys")
    op.drop_table("events")
"""add knowledge capture audit

Revision ID: add_knowledge_capture_audit
Revises: c782ae65766b
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_knowledge_capture_audit"
down_revision: Union[str, Sequence[str], None] = "c782ae65766b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_capture_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execution_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "article_number",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "article_sys_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "qdrant_point_ids",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_knowledge_capture_audit_execution_reference",
        "knowledge_capture_audit",
        ["execution_reference"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_capture_audit_execution_reference",
        table_name="knowledge_capture_audit",
    )

    op.drop_table("knowledge_capture_audit")
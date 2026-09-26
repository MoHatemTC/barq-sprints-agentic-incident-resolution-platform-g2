"""add consumed flag to approvals

Revision ID: add_consumed_approvals
Revises: c782ae65766b
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_consumed_approvals"
down_revision: Union[str, Sequence[str], None] = (
    "c782ae65766b",
    "add_knowledge_capture_audit",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add single-use consumption flag to approval records."""
    op.add_column(
        "approvals",
        sa.Column(
            "consumed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Remove the server default after existing rows have been backfilled.
    op.alter_column(
        "approvals",
        "consumed",
        server_default=None,
    )


def downgrade() -> None:
    """Remove the approval consumption flag."""
    op.drop_column("approvals", "consumed")
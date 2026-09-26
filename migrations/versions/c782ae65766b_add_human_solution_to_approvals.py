"""add human solution to approvals

Revision ID: c782ae65766b
Revises: 416136e54c32
Create Date: 2026-09-24 16:26:13.140593

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c782ae65766b'
down_revision: Union[str, Sequence[str], None] = '416136e54c32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Add human-authored resolution to approval records."""
    op.add_column(
        "approvals",
        sa.Column("human_solution", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove human-authored resolution from approval records."""
    op.drop_column("approvals", "human_solution")
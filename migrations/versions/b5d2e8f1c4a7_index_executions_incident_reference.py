"""index executions.incident_reference

The dashboard incident list looks up the latest execution for each incident
number on every poll; without an index that is a full table scan.

Revision ID: b5d2e8f1c4a7
Revises: a91f4c7e2b10
Create Date: 2026-09-29 12:00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b5d2e8f1c4a7"
down_revision: Union[str, Sequence[str], None] = "a91f4c7e2b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_executions_incident_reference",
        "executions",
        ["incident_reference"],
    )


def downgrade() -> None:
    op.drop_index("ix_executions_incident_reference", table_name="executions")

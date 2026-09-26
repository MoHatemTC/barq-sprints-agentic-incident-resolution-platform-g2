"""allow awaiting_approval execution status (S3.4)

Revision ID: a3f4c2d9e1b7
Revises: 416136e54c32
Create Date: 2026-09-25

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a3f4c2d9e1b7"
down_revision: Union[str, Sequence[str], None] = "416136e54c32"
branch_labels = None
depends_on = None


OLD_STATUSES = "('started', 'succeeded', 'failed', 'blocked', 'abandoned')"
NEW_STATUSES = "('started', 'succeeded', 'failed', 'blocked', 'abandoned', 'awaiting_approval')"


def upgrade() -> None:
    # A run paused at LangGraph interrupt() waits for a human decision
    op.drop_constraint("ck_executions_status", "executions", type_="check")
    op.create_check_constraint(
        "ck_executions_status",
        "executions",
        f"status IN {NEW_STATUSES}",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE executions SET status = 'blocked' WHERE status = 'awaiting_approval'"
    )
    op.drop_constraint("ck_executions_status", "executions", type_="check")
    op.create_check_constraint(
        "ck_executions_status",
        "executions",
        f"status IN {OLD_STATUSES}",
    )

"""add cost tracking columns to execution

Revision ID: 005_cost_tracking
Revises: b5d2e8f1c4a7
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '005_cost_tracking'
down_revision = 'b5d2e8f1c4a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('total_tokens_in', sa.Integer(), nullable=True))
    op.add_column('executions', sa.Column('total_tokens_out', sa.Integer(), nullable=True))
    op.add_column('executions', sa.Column('estimated_cost_usd', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'estimated_cost_usd')
    op.drop_column('executions', 'total_tokens_out')
    op.drop_column('executions', 'total_tokens_in')

"""add consumed flag to approvals

Revision ID: 1a2b3c4d5e6f
Revises: 416136e54c32
Create Date: 2026-09-25

The approval consume flow (permissions.is_approved) marks an approval
record as consumed in a single atomic UPDATE so that exactly one dispatch
can spend one grant. The old immutability trigger blocked every UPDATE;
it now permits only the one-shot consumed false->true transition while
still blocking every other UPDATE and every DELETE.
"""

from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: str = "416136e54c32"
branch_labels = None
depends_on = None


_CONSUME_AWARE_TRIGGER = """
CREATE OR REPLACE FUNCTION prevent_approval_update() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.consumed = true AND OLD.consumed = false AND
       NEW.execution_reference IS NOT DISTINCT FROM OLD.execution_reference AND
       NEW.evidence_presented IS NOT DISTINCT FROM OLD.evidence_presented AND
       NEW.reviewer_decision IS NOT DISTINCT FROM OLD.reviewer_decision AND
       NEW.decision_timestamp IS NOT DISTINCT FROM OLD.decision_timestamp AND
       NEW.reviewer_identity IS NOT DISTINCT FROM OLD.reviewer_identity
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'approval records are immutable';
END;
$$ LANGUAGE plpgsql;
"""

_STRICT_TRIGGER = """
CREATE OR REPLACE FUNCTION prevent_approval_update() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'approval records are immutable';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(_CONSUME_AWARE_TRIGGER)
    op.execute("DROP TRIGGER IF EXISTS approval_immutable ON approvals;")
    op.execute(
        """
        CREATE TRIGGER approval_immutable
        BEFORE UPDATE OR DELETE ON approvals
        FOR EACH ROW EXECUTE FUNCTION prevent_approval_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS approval_immutable ON approvals;")
    op.execute(_STRICT_TRIGGER)
    op.execute(
        """
        CREATE TRIGGER approval_immutable
        BEFORE UPDATE OR DELETE ON approvals
        FOR EACH ROW EXECUTE FUNCTION prevent_approval_update();
        """
    )
    op.drop_column("approvals", "consumed")
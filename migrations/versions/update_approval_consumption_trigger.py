"""allow one-time approval consumption

Revision ID: update_approval_consumption_trigger
Revises: add_consumed_approvals
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a91f4c7e2b10"

down_revision: Union[str, Sequence[str], None] = "add_consumed_approvals"

branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION prevent_approval_modification()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'approval records are immutable';
        END IF;

        IF TG_OP = 'UPDATE' THEN
            IF
                NEW.id IS DISTINCT FROM OLD.id
                OR NEW.execution_reference IS DISTINCT FROM OLD.execution_reference
                OR NEW.evidence_presented IS DISTINCT FROM OLD.evidence_presented
                OR NEW.human_solution IS DISTINCT FROM OLD.human_solution
                OR NEW.reviewer_decision IS DISTINCT FROM OLD.reviewer_decision
                OR NEW.decision_timestamp IS DISTINCT FROM OLD.decision_timestamp
                OR NEW.reviewer_identity IS DISTINCT FROM OLD.reviewer_identity
                OR (
                    OLD.consumed = true
                    AND NEW.consumed IS DISTINCT FROM OLD.consumed
                )
                OR (
                    OLD.consumed = false
                    AND NEW.consumed = false
                )
            THEN
                RAISE EXCEPTION
                    'approval records are immutable except for one-time consumption';
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS approval_immutable ON approvals;

    CREATE TRIGGER approval_immutable
    BEFORE UPDATE OR DELETE ON approvals
    FOR EACH ROW
    EXECUTE FUNCTION prevent_approval_modification();
    """)


def downgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION prevent_approval_modification()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'approval records are immutable';
    END;
    $$ LANGUAGE plpgsql;
    """)
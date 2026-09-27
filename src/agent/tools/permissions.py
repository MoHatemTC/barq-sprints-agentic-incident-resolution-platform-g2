"""Permission classes and the server-side approval gate for agent tools.

A tool's permission class decides whether invoking it requires a consumed
human approval against the Postgres ``approvals`` table (see
src/db/models.Approval). The check-and-consume is a single atomic SQL
UPDATE ... RETURNING, so two racing dispatches can never both pass the
gate for the same approval record.
"""

from enum import Enum, auto

from sqlalchemy import text

from src.db.database import SessionLocal


class PermissionClass(Enum):
    READ = auto()
    LOW_RISK_WRITE = auto()
    HIGH_RISK = auto()


def is_approved(execution_id: str, permission_class: PermissionClass) -> bool:
    """Return whether a dispatch for ``execution_id`` may run.

    READ and LOW_RISK_WRITE never require approval. HIGH_RISK requires an
    unconsumed, approved record for this exact execution id in the Postgres
    approvals table; the matching record is atomically marked as consumed in
    the same statement. Any ambiguity or missing data results in refusal.
    """
    if permission_class is PermissionClass.READ:
        return True
    if permission_class is PermissionClass.LOW_RISK_WRITE:
        return True
    if permission_class is not PermissionClass.HIGH_RISK:
        return False
    if not execution_id:
        return False

    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "UPDATE approvals SET consumed = true "
                "WHERE execution_reference = :execution_id "
                "AND consumed = false "
                "AND reviewer_decision = 'approved' "
                "RETURNING id"
            ),
            {"execution_id": execution_id},
        )
        row = result.fetchone()
        if row is None:
            db.rollback()
            return False
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()
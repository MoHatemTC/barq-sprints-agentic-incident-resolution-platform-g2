import pytest
from sqlalchemy.exc import IntegrityError

from src.db.database import SessionLocal
from src.db.models import Failure


def test_failure_cannot_reference_nonexistent_execution():

    db = SessionLocal()

    try:
        failure = Failure(
            execution_reference="nonexistent-execution-999",
            failing_node="load",
            error_class="ValueError",
            message="boom",
            retry_count=0,
        )

        db.add(failure)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    finally:
        db.close()
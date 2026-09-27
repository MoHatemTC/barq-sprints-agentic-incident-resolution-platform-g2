"""merge s3.4 awaiting_approval and s3.2 approval consumed heads

Revision ID: beb8ca5659be
Revises: a3f4c2d9e1b7, 1a2b3c4d5e6f
Create Date: 2026-09-27 12:10:14.010232

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'beb8ca5659be'
down_revision: Union[str, Sequence[str], None] = ('a3f4c2d9e1b7', '1a2b3c4d5e6f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
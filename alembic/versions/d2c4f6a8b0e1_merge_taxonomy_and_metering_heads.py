"""merge taxonomy wave and metering/session heads

The taxonomy wave and v3.43 account/metering work were developed from the
same released revision.  Keeping both parents here lets an existing taxonomy
test database apply the newer main-line migrations, while a production
database on main can apply the taxonomy chain.  Both paths converge on one
Alembic head without replaying either branch.

Revision ID: d2c4f6a8b0e1
Revises: a7d4e9f2c1b6, a7e2f95c1d40
Create Date: 2026-09-02

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "d2c4f6a8b0e1"
down_revision: Union[str, Sequence[str], None] = (
    "a7d4e9f2c1b6",
    "a7e2f95c1d40",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join two fully applied schema branches; no data operation is needed."""


def downgrade() -> None:
    """Removing the merge marker exposes both parent heads again."""

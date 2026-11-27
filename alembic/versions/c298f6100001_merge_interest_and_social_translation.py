"""Merge custom interest and social translation migration histories.

Revision ID: c298f6100001
Revises: c298e5100001, e349b6d2a8c1
Create Date: 2026-11-26
"""

revision = "c298f6100001"
down_revision = ("c298e5100001", "e349b6d2a8c1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the independently applied parents without changing their data."""


def downgrade() -> None:
    """Expose both parents without changing their data."""

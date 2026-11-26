"""Merge custom interests and personal digest refresh migrations.

Revision ID: c298e5100001
Revises: c298d4100001, c3e5a91f7d24
Create Date: 2026-11-26
"""

revision = "c298e5100001"
down_revision = ("c298d4100001", "c3e5a91f7d24")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parents apply independently; only join their revision histories."""


def downgrade() -> None:
    """Expose both parent revisions without changing data."""

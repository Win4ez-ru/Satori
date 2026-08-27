"""Create the reversible Stage 1 schema baseline.

Revision ID: 0001_foundation
Revises: None
Create Date: 2026-07-27

No domain or application tables belong to Stage 1. Alembic's version table is the only
database object created by this revision.
"""

from collections.abc import Sequence

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the Stage 1 baseline intentionally free of premature tables."""


def downgrade() -> None:
    """Reverse the empty Stage 1 baseline."""

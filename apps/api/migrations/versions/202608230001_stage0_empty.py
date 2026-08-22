"""stage0 empty baseline

Revision ID: 202608230001
Revises:
Create Date: 2026-08-23 00:01:00.000000
"""

from collections.abc import Sequence

revision: str = "202608230001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

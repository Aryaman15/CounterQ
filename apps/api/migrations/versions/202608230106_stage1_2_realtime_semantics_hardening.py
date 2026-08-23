"""stage1 2 realtime semantics hardening

Revision ID: 202608230106
Revises: 202608230105
Create Date: 2026-08-23 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608230106"
down_revision: str | Sequence[str] | None = "202608230105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_prompt_deliveries_one_started_per_session",
        "interviewer_prompt_deliveries",
        ["interview_session_id"],
        unique=True,
        postgresql_where=sa.text("delivery_state = 'STARTED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_prompt_deliveries_one_started_per_session",
        table_name="interviewer_prompt_deliveries",
    )

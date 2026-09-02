"""Stage 5B stable Assessment evaluation identity.

Revision ID: 202609020113
Revises: 202609020112
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609020113"
down_revision: str | Sequence[str] | None = "202609020112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("evaluation_key", sa.String(length=71), nullable=True))
    op.create_check_constraint(
        "ck_assessments_evaluation_key_format",
        "assessments",
        "evaluation_key IS NULL OR evaluation_key ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_index(
        "uq_assessments_session_evaluation_key",
        "assessments",
        ["interview_session_id", "evaluation_key"],
        unique=True,
        postgresql_where=sa.text("evaluation_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_assessments_session_evaluation_key", table_name="assessments")
    op.drop_constraint("ck_assessments_evaluation_key_format", "assessments", type_="check")
    op.drop_column("assessments", "evaluation_key")

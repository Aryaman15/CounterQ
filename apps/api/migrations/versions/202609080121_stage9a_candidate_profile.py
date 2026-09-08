"""Stage 9A candidate profile.

Revision ID: 202609080121
Revises: 202609060120
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609080121"
down_revision: str | Sequence[str] | None = "202609060120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("preferred_language", sa.String(16), nullable=False),
        sa.Column("default_interview_mode", sa.String(32), nullable=False),
        sa.Column("interview_level", sa.String(32), nullable=False),
        sa.Column("target_role", sa.String(160)),
        sa.Column("timezone", sa.String(64)),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_values("preferred_language", ("cpp", "java", "python")),
            name=op.f("ck_candidate_profiles_preferred_language"),
        ),
        sa.CheckConstraint(
            _in_values("default_interview_mode", ("COACH", "SIMULATION")),
            name=op.f("ck_candidate_profiles_default_interview_mode"),
        ),
        sa.CheckConstraint(
            _in_values("interview_level", ("INTERN", "NEW_GRAD", "EARLY_CAREER")),
            name=op.f("ck_candidate_profiles_interview_level"),
        ),
        sa.CheckConstraint(
            "profile_version > 0",
            name=op.f("ck_candidate_profiles_profile_version_positive"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("candidate_profiles")

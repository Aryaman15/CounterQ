"""Stage 6B durable AssessmentUnit evaluation completion identity.

Revision ID: 202609040118
Revises: 202609040117
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609040118"
down_revision: str | Sequence[str] | None = "202609040117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessment_unit_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_key", sa.String(length=71), nullable=False),
        sa.Column("unit_kind", sa.String(length=64), nullable=False),
        sa.Column("evaluator_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("successful_ai_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "unit_key ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_assessment_unit_evaluations_unit_key_format",
        ),
        sa.CheckConstraint(
            "length(btrim(unit_kind)) > 0",
            name="ck_assessment_unit_evaluations_unit_kind_nonempty",
        ),
        sa.CheckConstraint(
            "finding_count >= 0",
            name="ck_assessment_unit_evaluations_finding_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evaluator_policy_version_id"],
            ["ai_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["successful_ai_invocation_id"],
            ["ai_invocations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "successful_ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_assessment_unit_evaluations_session_ai_invocation",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "unit_key",
            "evaluator_policy_version_id",
            name="uq_assessment_unit_evaluations_session_unit_policy",
        ),
    )
    op.create_index(
        "ix_assessment_unit_evaluations_session_completed_at",
        "assessment_unit_evaluations",
        ["interview_session_id", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assessment_unit_evaluations_session_completed_at",
        table_name="assessment_unit_evaluations",
    )
    op.drop_table("assessment_unit_evaluations")

"""Stage 9E custom problem preparation and launch provenance.

Revision ID: 202609110123
Revises: 202609110122
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609110123"
down_revision: str | Sequence[str] | None = "202609110122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_problem_preparations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("original_problem_text", sa.Text(), nullable=False),
        sa.Column("normalized_content_hash", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("preparation_policy_key", sa.String(length=128), nullable=False),
        sa.Column("preparation_policy_version", sa.String(length=64), nullable=False),
        sa.Column("quality_gate_version", sa.String(length=64), nullable=False),
        sa.Column("operational_status", sa.String(length=32), nullable=False),
        sa.Column("quality_outcome", sa.String(length=32), nullable=True),
        sa.Column("candidate_message", sa.Text(), nullable=True),
        sa.Column(
            "candidate_reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("normalization_ai_invocation_id", sa.Uuid(), nullable=True),
        sa.Column("pack_ai_invocation_id", sa.Uuid(), nullable=True),
        sa.Column("prepared_problem_version_id", sa.Uuid(), nullable=True),
        sa.Column("prepared_pack_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "sandbox_validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_count >= 0",
            name=op.f("ck_custom_problem_preparations_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "(operational_status = 'COMPLETED' AND quality_outcome IS NOT NULL) OR "
            "(operational_status <> 'COMPLETED' AND quality_outcome IS NULL)",
            name=op.f("ck_custom_problem_preparations_completed_has_quality_outcome"),
        ),
        sa.CheckConstraint(
            "operational_status IN ('PENDING', 'PROCESSING', 'FAILED', 'COMPLETED')",
            name=op.f("ck_custom_problem_preparations_operational_status"),
        ),
        sa.CheckConstraint(
            "quality_outcome IS NULL OR quality_outcome IN "
            "('READY', 'NEEDS_CORRECTION', 'REJECTED')",
            name=op.f("ck_custom_problem_preparations_quality_outcome"),
        ),
        sa.CheckConstraint(
            "(quality_outcome = 'READY' AND prepared_problem_version_id IS NOT NULL "
            "AND prepared_pack_version_id IS NOT NULL) OR "
            "(quality_outcome IS DISTINCT FROM 'READY' AND prepared_problem_version_id IS NULL "
            "AND prepared_pack_version_id IS NULL)",
            name=op.f("ck_custom_problem_preparations_ready_has_prepared_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["normalization_ai_invocation_id"], ["ai_invocations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pack_ai_invocation_id"], ["ai_invocations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["prepared_pack_version_id", "prepared_problem_version_id"],
            ["interview_pack_versions.id", "interview_pack_versions.problem_version_id"],
            name="fk_custom_preparations_pack_problem_version",
        ),
        sa.ForeignKeyConstraint(
            ["prepared_problem_version_id"], ["problem_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_problem_preparations")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_custom_preparations_user_key"
        ),
    )
    op.add_column(
        "interview_configurations",
        sa.Column("custom_problem_preparation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_configurations_custom_preparation",
        "interview_configurations",
        "custom_problem_preparations",
        ["custom_problem_preparation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_interview_configurations_custom_preparation_source"),
        "interview_configurations",
        "(problem_source <> 'CUSTOM' AND custom_problem_preparation_id IS NULL) OR "
        "(problem_source = 'CUSTOM' AND custom_problem_preparation_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_interview_configurations_custom_preparation_source"),
        "interview_configurations",
        type_="check",
    )
    op.drop_constraint(
        "fk_interview_configurations_custom_preparation",
        "interview_configurations",
        type_="foreignkey",
    )
    op.drop_column("interview_configurations", "custom_problem_preparation_id")
    op.drop_table("custom_problem_preparations")

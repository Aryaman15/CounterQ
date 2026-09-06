"""Stage 8A deterministic Mastery foundation and retest workflow.

Revision ID: 202609060120
Revises: 202609050119
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609060120"
down_revision: str | Sequence[str] | None = "202609050119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


PRE_STAGE8_OUTBOX_TYPES = (
    "FINALIZE_SESSION_EVIDENCE",
    "GENERATE_SESSION_REPORT",
    "GENERATE_COUNTERMAP",
)
STAGE8_OUTBOX_TYPES = (*PRE_STAGE8_OUTBOX_TYPES, "RECALCULATE_MASTERY")
MASTERY_STATES = ("UNTESTED", "EXPOSED", "WEAK", "DEVELOPING", "STRONG")
CONTRIBUTIONS = ("SUPPORTING", "CONTRADICTING", "LEARNING_LIMITED")
RETEST_STATUSES = (
    "PENDING",
    "SCHEDULED",
    "ATTEMPTED",
    "SATISFIED",
    "DISMISSED",
    "SUPERSEDED",
)


def _current_projection_table(*, table: str, target_column: str, target_table: str) -> None:
    op.create_table(
        table,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(target_column, postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True)),
        sa.Column("mastery_policy_version", sa.String(96), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("supporting_evidence_count", sa.Integer(), nullable=False),
        sa.Column("context_diversity", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_values("state", MASTERY_STATES), name=op.f(f"ck_{table}_state")),
        sa.CheckConstraint(
            "projection_version > 0",
            name=op.f(f"ck_{table}_projection_version_positive"),
        ),
        sa.CheckConstraint(
            "supporting_evidence_count >= 0",
            name=op.f(f"ck_{table}_support_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "context_diversity >= 0",
            name=op.f(f"ck_{table}_context_diversity_nonnegative"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint([target_column], [f"{target_table}.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", target_column, name=f"uq_{table}_user_{target_column.removesuffix('_id')}"
        ),
    )
    op.create_index(f"ix_{table}_user_state", table, ["user_id", "state"])


def _mastery_evidence_table(*, table: str, current_table: str, target_column: str) -> None:
    op.create_table(
        table,
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(target_column, postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contribution_classification", sa.String(32), nullable=False),
        sa.Column("context_key", sa.String(320), nullable=False),
        sa.Column("admitted_policy_version", sa.String(96), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in_values("contribution_classification", CONTRIBUTIONS),
            name=op.f(f"ck_{table}_contribution_classification"),
        ),
        sa.CheckConstraint(
            "length(btrim(context_key)) > 0",
            name=op.f(f"ck_{table}_context_key_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", target_column],
            [f"{current_table}.user_id", f"{current_table}.{target_column}"],
            name=f"fk_{table}_current",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", target_column, "evidence_id"),
    )
    op.create_index(f"ix_{table}_evidence", table, ["evidence_id"])


def upgrade() -> None:
    op.drop_constraint(op.f("ck_outbox_events_event_type"), "outbox_events", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", STAGE8_OUTBOX_TYPES),
    )

    _current_projection_table(
        table="concept_mastery", target_column="concept_id", target_table="concepts"
    )
    _current_projection_table(
        table="skill_mastery",
        target_column="skill_dimension_id",
        target_table="skill_dimensions",
    )
    _mastery_evidence_table(
        table="concept_mastery_evidence",
        current_table="concept_mastery",
        target_column="concept_id",
    )
    _mastery_evidence_table(
        table="skill_mastery_evidence",
        current_table="skill_mastery",
        target_column="skill_dimension_id",
    )

    op.create_table(
        "mastery_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True)),
        sa.Column("skill_dimension_id", postgresql.UUID(as_uuid=True)),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("mastery_policy_version", sa.String(96), nullable=False),
        sa.Column("transition_key", sa.String(71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_values("target_type", ("CONCEPT", "SKILL")),
            name=op.f("ck_mastery_transitions_target_type"),
        ),
        sa.CheckConstraint(
            _in_values("from_state", MASTERY_STATES),
            name=op.f("ck_mastery_transitions_from_state"),
        ),
        sa.CheckConstraint(
            _in_values("to_state", MASTERY_STATES),
            name=op.f("ck_mastery_transitions_to_state"),
        ),
        sa.CheckConstraint(
            "from_state <> to_state", name=op.f("ck_mastery_transitions_state_changes")
        ),
        sa.CheckConstraint(
            "(target_type = 'CONCEPT' AND concept_id IS NOT NULL "
            "AND skill_dimension_id IS NULL) OR "
            "(target_type = 'SKILL' AND concept_id IS NULL "
            "AND skill_dimension_id IS NOT NULL)",
            name=op.f("ck_mastery_transitions_target_xor"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["skill_dimension_id"], ["skill_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transition_key", name="uq_mastery_transitions_transition_key"),
    )
    op.create_index(
        "ix_mastery_transitions_user_created", "mastery_transitions", ["user_id", "created_at"]
    )
    op.create_table(
        "mastery_transition_evidence",
        sa.Column("mastery_transition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mastery_transition_id"], ["mastery_transitions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mastery_transition_id", "evidence_id"),
    )

    op.create_table(
        "retest_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("breakpoint_id", postgresql.UUID(as_uuid=True)),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True)),
        sa.Column("skill_dimension_id", postgresql.UUID(as_uuid=True)),
        sa.Column("recommended_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("generation_policy_version", sa.String(96), nullable=False),
        sa.Column("recommendation_key", sa.String(320), nullable=False),
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
            _in_values("status", RETEST_STATUSES),
            name=op.f("ck_retest_recommendations_status"),
        ),
        sa.CheckConstraint(
            "concept_id IS NOT NULL OR skill_dimension_id IS NOT NULL",
            name=op.f("ck_retest_recommendations_target_present"),
        ),
        sa.CheckConstraint(
            "priority >= 0", name=op.f("ck_retest_recommendations_priority_nonnegative")
        ),
        sa.CheckConstraint(
            "length(btrim(strategy)) > 0",
            name=op.f("ck_retest_recommendations_strategy_nonempty"),
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) > 0",
            name=op.f("ck_retest_recommendations_rationale_nonempty"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["breakpoint_id"], ["breakpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["skill_dimension_id"], ["skill_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recommendation_key", name="uq_retest_recommendations_key"),
    )
    op.create_index(
        "ix_retest_recommendations_user_status",
        "retest_recommendations",
        ["user_id", "status", "recommended_after"],
    )

    op.create_table(
        "retest_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retest_recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interviewer_prompt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(64)),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_retest_attempts_completion_after_start"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR length(btrim(outcome)) > 0",
            name=op.f("ck_retest_attempts_outcome_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["retest_recommendation_id"], ["retest_recommendations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "interviewer_prompt_id"],
            ["interviewer_prompts.interview_session_id", "interviewer_prompts.id"],
            name="fk_retest_attempts_session_prompt",
            ondelete="SET NULL (interviewer_prompt_id)",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "retest_recommendation_id",
            "interview_session_id",
            name="uq_retest_attempts_recommendation_session",
        ),
    )
    op.create_table(
        "retest_attempt_evidence",
        sa.Column("retest_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["retest_attempt_id"], ["retest_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("retest_attempt_id", "evidence_id"),
    )


def downgrade() -> None:
    op.drop_table("retest_attempt_evidence")
    op.drop_table("retest_attempts")
    op.drop_index("ix_retest_recommendations_user_status", table_name="retest_recommendations")
    op.drop_table("retest_recommendations")
    op.drop_table("mastery_transition_evidence")
    op.drop_index("ix_mastery_transitions_user_created", table_name="mastery_transitions")
    op.drop_table("mastery_transitions")
    op.drop_index("ix_skill_mastery_evidence_evidence", table_name="skill_mastery_evidence")
    op.drop_table("skill_mastery_evidence")
    op.drop_index("ix_concept_mastery_evidence_evidence", table_name="concept_mastery_evidence")
    op.drop_table("concept_mastery_evidence")
    op.drop_index("ix_skill_mastery_user_state", table_name="skill_mastery")
    op.drop_table("skill_mastery")
    op.drop_index("ix_concept_mastery_user_state", table_name="concept_mastery")
    op.drop_table("concept_mastery")
    op.drop_constraint(op.f("ck_outbox_events_event_type"), "outbox_events", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", PRE_STAGE8_OUTBOX_TYPES),
    )

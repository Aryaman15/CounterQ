"""Stage 6A Coach assistance prompt provenance and delivery budgets.

Revision ID: 202609030115
Revises: 202609030114
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609030115"
down_revision: str | Sequence[str] | None = "202609030114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


ASSISTANCE_TYPES = (
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
    "DEBUGGING_HINT",
    "CORRECTNESS_FEEDBACK",
)
HINT_LEVELS = (
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
)
ASSISTANCE_TRIGGERS = (
    "CANDIDATE_REQUEST",
    "POST_PROBE_GAP",
    "STUCK_POLICY",
    "DEBUGGING_STALL",
    "CORRECTNESS_REQUEST",
)
PRE_STAGE6_EVENT_TYPES = (
    "TRANSCRIPT_FINALIZED",
    "COUNTERQ_UTTERANCE_DELIVERED",
    "CODE_SNAPSHOT_CREATED",
    "MEANINGFUL_CODE_CHANGE",
    "RUN_CLICKED",
    "COMPILE_COMPLETED",
    "TEST_COMPLETED",
    "STAGE_CHANGED",
    "CANDIDATE_DECLARED_DONE",
    "CANDIDATE_INTERRUPTED_COUNTERQ",
    "COUNTERQ_INTERRUPTED_CANDIDATE",
    "REALTIME_DISCONNECTED",
    "REALTIME_RECONNECTED",
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        _in_values(
            "event_type", (*PRE_STAGE6_EVENT_TYPES, "CANDIDATE_ASSISTANCE_REQUESTED")
        ),
    )
    op.add_column("interviewer_prompts", sa.Column("assistance_type", sa.String(64)))
    op.add_column("interviewer_prompts", sa.Column("hint_level", sa.String(64)))
    op.add_column("interviewer_prompts", sa.Column("assistance_trigger", sa.String(64)))
    op.add_column(
        "interviewer_prompts",
        sa.Column("target_event_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("interviewer_prompts", sa.Column("source_event_watermark", sa.BigInteger()))
    op.add_column(
        "interviewer_prompts",
        sa.Column("source_code_snapshot_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "interviewer_prompts",
        sa.Column(
            "invites_guided_retry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_assistance_type"),
        "interviewer_prompts",
        f"assistance_type IS NULL OR {_in_values('assistance_type', ASSISTANCE_TYPES)}",
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_hint_level"),
        "interviewer_prompts",
        f"hint_level IS NULL OR {_in_values('hint_level', HINT_LEVELS)}",
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_assistance_trigger"),
        "interviewer_prompts",
        "assistance_trigger IS NULL OR "
        f"{_in_values('assistance_trigger', ASSISTANCE_TRIGGERS)}",
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_assistance_watermark_positive"),
        "interviewer_prompts",
        "source_event_watermark IS NULL OR source_event_watermark > 0",
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_assistance_matches_instruction"),
        "interviewer_prompts",
        "(assistance_type IS NOT NULL AND kind = 'INSTRUCTION' "
        "AND hint_level IS NOT NULL AND assistance_trigger IS NOT NULL "
        "AND source_event_watermark IS NOT NULL) OR "
        "(assistance_type IS NULL AND hint_level IS NULL "
        "AND assistance_trigger IS NULL AND source_event_watermark IS NULL "
        "AND invites_guided_retry = false)",
    )
    op.create_check_constraint(
        op.f("ck_interviewer_prompts_assistance_has_target"),
        "interviewer_prompts",
        "assistance_type IS NULL OR target_claim_id IS NOT NULL OR target_event_id IS NOT NULL "
        "OR target_concept_id IS NOT NULL OR target_skill_dimension_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_interviewer_prompts_target_concept",
        "interviewer_prompts",
        "concepts",
        ["target_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interviewer_prompts_target_skill",
        "interviewer_prompts",
        "skill_dimensions",
        ["target_skill_dimension_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interviewer_prompts_session_target_event",
        "interviewer_prompts",
        "interview_events",
        ["interview_session_id", "target_event_id"],
        ["interview_session_id", "id"],
        ondelete="SET NULL (target_event_id)",
    )
    op.create_foreign_key(
        "fk_interviewer_prompts_session_code_snapshot",
        "interviewer_prompts",
        "code_snapshots",
        ["interview_session_id", "source_code_snapshot_id"],
        ["interview_session_id", "id"],
        ondelete="SET NULL (source_code_snapshot_id)",
    )
    op.create_index(
        "uq_interviewer_prompts_assistance_request",
        "interviewer_prompts",
        ["interview_session_id", "target_event_id"],
        unique=True,
        postgresql_where=sa.text("assistance_type IS NOT NULL"),
    )

    for name in (
        "max_assistance_interventions",
        "assistance_interventions_used",
        "max_structural_hints",
        "structural_hints_used",
        "max_direct_teaching_interventions",
        "direct_teaching_interventions_used",
        "max_guided_retries",
        "guided_retries_used",
    ):
        op.add_column(
            "session_budgets",
            sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.create_check_constraint(
        op.f("ck_session_budgets_max_assistance_interventions_nonnegative"),
        "session_budgets",
        "max_assistance_interventions >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_assistance_interventions_used_within_max"),
        "session_budgets",
        "assistance_interventions_used >= 0 AND "
        "assistance_interventions_used <= max_assistance_interventions",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_max_structural_hints_nonnegative"),
        "session_budgets",
        "max_structural_hints >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_structural_hints_used_within_max"),
        "session_budgets",
        "structural_hints_used >= 0 AND structural_hints_used <= max_structural_hints",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_max_direct_teaching_interventions_nonnegative"),
        "session_budgets",
        "max_direct_teaching_interventions >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_direct_teaching_interventions_used_within_max"),
        "session_budgets",
        "direct_teaching_interventions_used >= 0 AND "
        "direct_teaching_interventions_used <= max_direct_teaching_interventions",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_max_guided_retries_nonnegative"),
        "session_budgets",
        "max_guided_retries >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_guided_retries_used_within_max"),
        "session_budgets",
        "guided_retries_used >= 0 AND guided_retries_used <= max_guided_retries",
    )


def downgrade() -> None:
    for constraint in (
        "guided_retries_used_within_max",
        "max_guided_retries_nonnegative",
        "direct_teaching_interventions_used_within_max",
        "max_direct_teaching_interventions_nonnegative",
        "structural_hints_used_within_max",
        "max_structural_hints_nonnegative",
        "assistance_interventions_used_within_max",
        "max_assistance_interventions_nonnegative",
    ):
        op.drop_constraint(
            op.f(f"ck_session_budgets_{constraint}"),
            "session_budgets",
            type_="check",
        )
    for name in (
        "guided_retries_used",
        "max_guided_retries",
        "direct_teaching_interventions_used",
        "max_direct_teaching_interventions",
        "structural_hints_used",
        "max_structural_hints",
        "assistance_interventions_used",
        "max_assistance_interventions",
    ):
        op.drop_column("session_budgets", name)

    op.drop_index("uq_interviewer_prompts_assistance_request", table_name="interviewer_prompts")
    for constraint in (
        "fk_interviewer_prompts_session_code_snapshot",
        "fk_interviewer_prompts_session_target_event",
        "fk_interviewer_prompts_target_skill",
        "fk_interviewer_prompts_target_concept",
    ):
        op.drop_constraint(constraint, "interviewer_prompts", type_="foreignkey")
    for constraint in (
        "assistance_has_target",
        "assistance_matches_instruction",
        "assistance_trigger",
        "hint_level",
        "assistance_type",
    ):
        op.drop_constraint(
            op.f(f"ck_interviewer_prompts_{constraint}"),
            "interviewer_prompts",
            type_="check",
        )
    # Tolerate a local database that briefly ran an earlier uncommitted draft
    # of this revision before the positive-watermark invariant was added.
    op.execute(
        "ALTER TABLE interviewer_prompts DROP CONSTRAINT IF EXISTS "
        "ck_interviewer_prompts_assistance_watermark_positive"
    )
    for name in (
        "invites_guided_retry",
        "source_code_snapshot_id",
        "source_event_watermark",
        "target_event_id",
        "assistance_trigger",
        "hint_level",
        "assistance_type",
    ):
        op.drop_column("interviewer_prompts", name)
    op.execute(
        "DELETE FROM interview_events "
        "WHERE event_type = 'CANDIDATE_ASSISTANCE_REQUESTED'"
    )
    op.drop_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        _in_values("event_type", PRE_STAGE6_EVENT_TYPES),
    )

"""stage1 2 deterministic session runtime persistence

Revision ID: 202608230105
Revises: 202608230104
Create Date: 2026-08-23 08:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608230105"
down_revision: str | Sequence[str] | None = "202608230104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


INTERVIEW_STAGES = (
    "SETUP",
    "INTRODUCTION",
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DISCOVERY",
    "APPROACH_DEFENSE",
    "IMPLEMENTATION",
    "TESTING_DEBUGGING",
    "COMPLEXITY_EDGE_CASES",
    "CONSTRAINT_MUTATION",
    "FINAL_DEFENSE",
    "WRAP_UP",
    "COMPLETED",
)


def upgrade() -> None:
    op.create_table(
        "interview_stage_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_stage", sa.String(length=64), nullable=False),
        sa.Column("to_stage", sa.String(length=64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=96), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transition_policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("from_stage", INTERVIEW_STAGES),
            name="ck_interview_stage_transitions_from_stage",
        ),
        sa.CheckConstraint(
            _in_values("to_stage", INTERVIEW_STAGES),
            name="ck_interview_stage_transitions_to_stage",
        ),
        sa.CheckConstraint(
            "state_version > 0",
            name="ck_interview_stage_transitions_state_version_positive",
        ),
        sa.CheckConstraint(
            "from_stage <> to_stage",
            name="ck_interview_stage_transitions_stage_changed",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["interview_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_stage_transitions_session_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "id",
            name="uq_stage_transitions_session_id",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "state_version",
            name="uq_stage_transitions_session_state_version",
        ),
        sa.UniqueConstraint("event_id", name="uq_stage_transitions_event"),
    )
    op.create_index(
        "ix_stage_transitions_session_state_version",
        "interview_stage_transitions",
        ["interview_session_id", "state_version"],
    )

    op.create_check_constraint(
        "ck_interviewer_prompts_examiner_decision_matches_origin",
        "interviewer_prompts",
        "(origin = 'EXAMINER_DECISION' AND examiner_decision_id IS NOT NULL) OR "
        "(origin <> 'EXAMINER_DECISION' AND examiner_decision_id IS NULL)",
    )
    _add_set_null_fk(
        "interviewer_prompts",
        "fk_interviewer_prompts_session_stage_transition",
        ("interview_session_id", "source_stage_transition_id"),
        "interview_stage_transitions",
        ("interview_session_id", "id"),
        ("source_stage_transition_id",),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_interviewer_prompts_session_stage_transition",
        "interviewer_prompts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_interviewer_prompts_examiner_decision_matches_origin",
        "interviewer_prompts",
        type_="check",
    )
    op.drop_index(
        "ix_stage_transitions_session_state_version",
        table_name="interview_stage_transitions",
    )
    op.drop_table("interview_stage_transitions")


def _add_set_null_fk(
    source_table: str,
    constraint_name: str,
    source_columns: tuple[str, ...],
    target_table: str,
    target_columns: tuple[str, ...],
    null_columns: tuple[str, ...],
) -> None:
    source_cols = ", ".join(source_columns)
    target_cols = ", ".join(target_columns)
    null_cols = ", ".join(null_columns)
    op.execute(
        f"""
        ALTER TABLE {source_table}
        ADD CONSTRAINT {constraint_name}
        FOREIGN KEY ({source_cols})
        REFERENCES {target_table} ({target_cols})
        ON DELETE SET NULL ({null_cols})
        """
    )

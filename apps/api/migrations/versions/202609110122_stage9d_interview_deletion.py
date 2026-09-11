"""Stage 9D interview deletion integrity.

Revision ID: 202609110122
Revises: 202609080121
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609110122"
down_revision: str | Sequence[str] | None = "202609080121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRE_STAGE9D_OUTBOX_TYPES = (
    "FINALIZE_SESSION_EVIDENCE",
    "GENERATE_SESSION_REPORT",
    "GENERATE_COUNTERMAP",
    "RECALCULATE_MASTERY",
)
STAGE9D_OUTBOX_TYPES = (*PRE_STAGE9D_OUTBOX_TYPES, "DELETE_INTERVIEW")


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_outbox_events_event_type"), "outbox_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", STAGE9D_OUTBOX_TYPES),
    )

    op.drop_constraint(
        op.f("fk_breakpoints_first_detected_session_id_interview_sessions"),
        "breakpoints",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_breakpoints_session_user", "breakpoints", type_="foreignkey"
    )
    op.alter_column(
        "breakpoints", "first_detected_session_id", existing_type=sa.Uuid(), nullable=True
    )
    op.create_foreign_key(
        "fk_breakpoints_session_user",
        "breakpoints",
        "interview_sessions",
        ["first_detected_session_id", "user_id"],
        ["id", "user_id"],
        ondelete="SET NULL (first_detected_session_id)",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM breakpoints WHERE first_detected_session_id IS NULL"
    )
    op.drop_constraint(
        "fk_breakpoints_session_user", "breakpoints", type_="foreignkey"
    )
    op.alter_column(
        "breakpoints", "first_detected_session_id", existing_type=sa.Uuid(), nullable=False
    )
    op.create_foreign_key(
        op.f("fk_breakpoints_first_detected_session_id_interview_sessions"),
        "breakpoints",
        "interview_sessions",
        ["first_detected_session_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_breakpoints_session_user",
        "breakpoints",
        "interview_sessions",
        ["first_detected_session_id", "user_id"],
        ["id", "user_id"],
    )

    op.drop_constraint(
        op.f("ck_outbox_events_event_type"), "outbox_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", PRE_STAGE9D_OUTBOX_TYPES),
    )

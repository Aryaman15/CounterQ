"""stage1 1a provenance integrity hardening

Revision ID: 202608230102
Revises: 202608230101
Create Date: 2026-08-23 05:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202608230102"
down_revision: str | Sequence[str] | None = "202608230101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_interview_events_session_id",
        "interview_events",
        ["interview_session_id", "id"],
    )
    op.create_unique_constraint(
        "uq_code_snapshots_session_id",
        "code_snapshots",
        ["interview_session_id", "id"],
    )
    op.create_foreign_key(
        "fk_transcript_segments_session_event",
        "transcript_segments",
        "interview_events",
        ["interview_session_id", "interview_event_id"],
        ["interview_session_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_code_snapshots_session_created_from_event",
        "code_snapshots",
        "interview_events",
        ["interview_session_id", "created_from_event_id"],
        ["interview_session_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_interview_events_session_code_snapshot",
        "interview_events",
        "code_snapshots",
        ["interview_session_id", "code_snapshot_id"],
        ["interview_session_id", "id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_interview_events_session_code_snapshot",
        "interview_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_code_snapshots_session_created_from_event",
        "code_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_transcript_segments_session_event",
        "transcript_segments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_code_snapshots_session_id",
        "code_snapshots",
        type_="unique",
    )
    op.drop_constraint(
        "uq_interview_events_session_id",
        "interview_events",
        type_="unique",
    )

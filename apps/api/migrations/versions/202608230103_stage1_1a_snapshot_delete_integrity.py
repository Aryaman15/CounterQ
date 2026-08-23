"""stage1 1a snapshot delete integrity cleanup

Revision ID: 202608230103
Revises: 202608230102
Create Date: 2026-08-23 06:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202608230103"
down_revision: str | Sequence[str] | None = "202608230102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_interview_events_session_code_snapshot",
        "interview_events",
        type_="foreignkey",
    )
    op.execute(
        """
        ALTER TABLE interview_events
        ADD CONSTRAINT fk_interview_events_session_code_snapshot
        FOREIGN KEY (interview_session_id, code_snapshot_id)
        REFERENCES code_snapshots (interview_session_id, id)
        ON DELETE SET NULL (code_snapshot_id)
        """
    )
    op.execute(
        """
        ALTER TABLE code_snapshots
        ADD CONSTRAINT fk_code_snapshots_session_parent_snapshot
        FOREIGN KEY (interview_session_id, parent_snapshot_id)
        REFERENCES code_snapshots (interview_session_id, id)
        ON DELETE SET NULL (parent_snapshot_id)
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_code_snapshots_session_parent_snapshot",
        "code_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_interview_events_session_code_snapshot",
        "interview_events",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_interview_events_session_code_snapshot",
        "interview_events",
        "code_snapshots",
        ["interview_session_id", "code_snapshot_id"],
        ["interview_session_id", "id"],
        ondelete="SET NULL",
    )

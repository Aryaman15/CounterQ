"""enforce exact Interview Pack and ProblemVersion session binding

Revision ID: 202608310110
Revises: 202608260109
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202608310110"
down_revision: str | None = "202608260109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_interview_pack_versions_id_problem_version",
        "interview_pack_versions",
        ["id", "problem_version_id"],
    )
    op.create_foreign_key(
        "fk_interview_sessions_pack_problem_version",
        "interview_sessions",
        "interview_pack_versions",
        ["interview_pack_version_id", "problem_version_id"],
        ["id", "problem_version_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_interview_sessions_pack_problem_version",
        "interview_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_interview_pack_versions_id_problem_version",
        "interview_pack_versions",
        type_="unique",
    )

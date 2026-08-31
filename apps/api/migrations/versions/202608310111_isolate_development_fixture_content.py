"""isolate synthetic development fixture content from curated catalog

Revision ID: 202608310111
Revises: 202608310110
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608310111"
down_revision: str | None = "202608310110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE problems
            SET source_type = 'DEVELOPMENT_FIXTURE'
            WHERE id IN (
                SELECT DISTINCT problem_versions.problem_id
                FROM problem_versions
                JOIN interview_pack_versions
                  ON interview_pack_versions.problem_version_id = problem_versions.id
                WHERE interview_pack_versions.preparation_policy_key = 'stage1_runtime_fixture'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE interview_configurations
            SET problem_source = 'DEVELOPMENT_FIXTURE'
            WHERE id IN (
                SELECT DISTINCT interview_sessions.interview_configuration_id
                FROM interview_sessions
                JOIN interview_pack_versions
                  ON interview_pack_versions.id = interview_sessions.interview_pack_version_id
                WHERE interview_pack_versions.preparation_policy_key = 'stage1_runtime_fixture'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE interview_configurations
            SET problem_source = 'CURATED'
            WHERE id IN (
                SELECT DISTINCT interview_sessions.interview_configuration_id
                FROM interview_sessions
                JOIN interview_pack_versions
                  ON interview_pack_versions.id = interview_sessions.interview_pack_version_id
                WHERE interview_pack_versions.preparation_policy_key = 'stage1_runtime_fixture'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE problems
            SET source_type = 'CURATED'
            WHERE id IN (
                SELECT DISTINCT problem_versions.problem_id
                FROM problem_versions
                JOIN interview_pack_versions
                  ON interview_pack_versions.problem_version_id = problem_versions.id
                WHERE interview_pack_versions.preparation_policy_key = 'stage1_runtime_fixture'
            )
            """
        )
    )

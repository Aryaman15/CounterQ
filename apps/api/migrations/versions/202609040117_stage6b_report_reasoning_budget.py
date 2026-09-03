"""Stage 6B dedicated Session Report reasoning budget.

Revision ID: 202609040117
Revises: 202609040116
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609040117"
down_revision: str | Sequence[str] | None = "202609040116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_budgets",
        sa.Column(
            "max_report_reasoning_calls",
            sa.Integer(),
            server_default=sa.text("4"),
            nullable=False,
        ),
    )
    op.add_column(
        "session_budgets",
        sa.Column(
            "report_reasoning_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_max_report_reasoning_calls_nonnegative"),
        "session_budgets",
        "max_report_reasoning_calls >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_report_reasoning_used_within_max"),
        "session_budgets",
        "report_reasoning_used >= 0 AND "
        "report_reasoning_used <= max_report_reasoning_calls",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_session_budgets_report_reasoning_used_within_max"),
        "session_budgets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_session_budgets_max_report_reasoning_calls_nonnegative"),
        "session_budgets",
        type_="check",
    )
    op.drop_column("session_budgets", "report_reasoning_used")
    op.drop_column("session_budgets", "max_report_reasoning_calls")

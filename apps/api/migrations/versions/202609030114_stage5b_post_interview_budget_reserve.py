"""Stage 5B post-interview deep-reasoning budget reserve.

Revision ID: 202609030114
Revises: 202609020113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609030114"
down_revision: str | Sequence[str] | None = "202609020113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_budgets",
        sa.Column(
            "reserved_post_interview_deep_reasoning_calls",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_post_eval_deep_reserve_nonnegative"),
        "session_budgets",
        "reserved_post_interview_deep_reasoning_calls >= 0",
    )
    op.create_check_constraint(
        op.f("ck_session_budgets_post_eval_deep_reserve_within_total"),
        "session_budgets",
        "reserved_post_interview_deep_reasoning_calls <= max_deep_reasoning_calls",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_session_budgets_post_eval_deep_reserve_within_total"),
        "session_budgets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_session_budgets_post_eval_deep_reserve_nonnegative"),
        "session_budgets",
        type_="check",
    )
    op.drop_column("session_budgets", "reserved_post_interview_deep_reasoning_calls")

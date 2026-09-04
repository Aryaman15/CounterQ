"""Stage 7A deterministic CounterMap projection and outbox event.

Revision ID: 202609050119
Revises: 202609040118
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609050119"
down_revision: str | Sequence[str] | None = "202609040118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


PRE_STAGE7_OUTBOX_TYPES = ("FINALIZE_SESSION_EVIDENCE", "GENERATE_SESSION_REPORT")
STAGE7_OUTBOX_TYPES = (*PRE_STAGE7_OUTBOX_TYPES, "GENERATE_COUNTERMAP")
PROJECTION_STATUSES = ("BUILDING", "READY", "FAILED", "STALE")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_outbox_events_event_type"), "outbox_events", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", STAGE7_OUTBOX_TYPES),
    )
    op.create_table(
        "countermap_projections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("generation_policy_version", sa.String(96), nullable=False),
        sa.Column("generation_request_key", sa.String(320), nullable=False),
        sa.Column("source_watermark", sa.BigInteger(), nullable=False),
        sa.Column("source_identity", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("graph_json", postgresql.JSONB()),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_failure_category", sa.String(128)),
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
            "projection_version > 0",
            name=op.f("ck_countermap_projections_projection_version_positive"),
        ),
        sa.CheckConstraint(
            _in_values("status", PROJECTION_STATUSES),
            name=op.f("ck_countermap_projections_status"),
        ),
        sa.CheckConstraint(
            "source_watermark >= 0",
            name=op.f("ck_countermap_projections_source_watermark_nonnegative"),
        ),
        sa.CheckConstraint(
            "source_identity ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_countermap_projections_source_identity_format"),
        ),
        sa.CheckConstraint(
            "NOT is_current OR status = 'READY'",
            name=op.f("ck_countermap_projections_current_is_ready"),
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (graph_json IS NOT NULL AND generated_at IS NOT NULL)",
            name=op.f("ck_countermap_projections_ready_is_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_session_id",
            "projection_version",
            name="uq_countermap_projections_session_version",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "generation_request_key",
            name="uq_countermap_projections_session_request",
        ),
    )
    op.create_index(
        "ix_countermap_projections_session_created",
        "countermap_projections",
        ["interview_session_id", "created_at"],
    )
    op.create_index(
        "uq_countermap_projections_one_current",
        "countermap_projections",
        ["interview_session_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_countermap_projections_one_current",
        table_name="countermap_projections",
    )
    op.drop_index(
        "ix_countermap_projections_session_created",
        table_name="countermap_projections",
    )
    op.drop_table("countermap_projections")
    op.drop_constraint(op.f("ck_outbox_events_event_type"), "outbox_events", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_events_event_type"),
        "outbox_events",
        _in_values("event_type", PRE_STAGE7_OUTBOX_TYPES),
    )

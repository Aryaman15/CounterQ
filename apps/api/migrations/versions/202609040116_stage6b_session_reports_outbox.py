"""Stage 6B Session Reports and transactional outbox.

Revision ID: 202609040116
Revises: 202609030115
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609040116"
down_revision: str | Sequence[str] | None = "202609030115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


PRE_STAGE6B_EVENT_TYPES = (
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
    "CANDIDATE_ASSISTANCE_REQUESTED",
)
REPORT_STATUSES = ("PENDING", "GENERATING", "READY", "FAILED", "SUPERSEDED")
REPORT_VALIDATION_STATUSES = ("PENDING", "PASSED", "FAILED")
OUTBOX_STATUSES = ("PENDING", "PUBLISHED", "PROCESSING", "COMPLETED", "RETRY", "FAILED")
OUTBOX_TYPES = ("FINALIZE_SESSION_EVIDENCE", "GENERATE_SESSION_REPORT")


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        _in_values("event_type", (*PRE_STAGE6B_EVENT_TYPES, "SESSION_COMPLETED")),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(96), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("deduplication_key", sa.String(320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("source_watermark", sa.BigInteger()),
        sa.CheckConstraint(
            _in_values("event_type", OUTBOX_TYPES), name=op.f("ck_outbox_events_event_type")
        ),
        sa.CheckConstraint(
            _in_values("status", OUTBOX_STATUSES), name=op.f("ck_outbox_events_status")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_outbox_events_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            "next_retry_at IS NULL OR next_retry_at >= created_at",
            name=op.f("ck_outbox_events_retry_not_before_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_index(
        "ix_outbox_events_dispatch",
        "outbox_events",
        ["status", "available_at", "next_retry_at"],
    )
    op.create_index(
        "ix_outbox_events_session_created",
        "outbox_events",
        ["interview_session_id", "created_at"],
    )

    op.create_table(
        "session_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("generation_request_key", sa.String(320), nullable=False),
        sa.Column("generation_policy_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("structured_report_json", postgresql.JSONB()),
        sa.Column("rendered_markdown", sa.Text()),
        sa.Column("generation_ai_invocation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_watermark", sa.BigInteger(), nullable=False),
        sa.Column("source_identity", sa.String(71), nullable=False),
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
            "report_version > 0", name=op.f("ck_session_reports_report_version_positive")
        ),
        sa.CheckConstraint(
            _in_values("status", REPORT_STATUSES), name=op.f("ck_session_reports_status")
        ),
        sa.CheckConstraint(
            _in_values("validation_status", REPORT_VALIDATION_STATUSES),
            name=op.f("ck_session_reports_validation_status"),
        ),
        sa.CheckConstraint(
            "source_watermark >= 0", name=op.f("ck_session_reports_source_watermark_nonnegative")
        ),
        sa.CheckConstraint(
            "source_identity ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_session_reports_source_identity_format"),
        ),
        sa.CheckConstraint(
            "NOT is_current OR status = 'READY'", name=op.f("ck_session_reports_current_is_ready")
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (structured_report_json IS NOT NULL "
            "AND generation_policy_version_id IS NOT NULL "
            "AND generation_ai_invocation_id IS NOT NULL "
            "AND generated_at IS NOT NULL AND validation_status = 'PASSED')",
            name=op.f("ck_session_reports_ready_is_complete"),
        ),
        sa.ForeignKeyConstraint(["generation_policy_version_id"], ["ai_policy_versions.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "generation_ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_session_reports_session_ai_invocation",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_session_id", "report_version", name="uq_session_reports_session_version"
        ),
    )
    op.create_index(
        "ix_session_reports_session_created",
        "session_reports",
        ["interview_session_id", "created_at"],
    )
    op.create_index(
        "uq_session_reports_one_current",
        "session_reports",
        ["interview_session_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index("uq_session_reports_one_current", table_name="session_reports")
    op.drop_index("ix_session_reports_session_created", table_name="session_reports")
    op.drop_table("session_reports")
    op.drop_index("ix_outbox_events_session_created", table_name="outbox_events")
    op.drop_index("ix_outbox_events_dispatch", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_interview_events_ck_interview_events_event_type"),
        "interview_events",
        _in_values("event_type", PRE_STAGE6B_EVENT_TYPES),
    )

"""stage1 1a session and observation persistence

Revision ID: 202608230101
Revises: 202608230001
Create Date: 2026-08-23 01:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608230101"
down_revision: str | Sequence[str] | None = "202608230001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


INTERVIEW_MODES = ("COACH", "SIMULATION")
INTERVIEW_LEVELS = ("INTERN", "NEW_GRAD", "EARLY_CAREER")
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
INTERVIEW_SESSION_STATUSES = (
    "READY",
    "ACTIVE",
    "RECONNECTING",
    "COMPLETED",
    "ABANDONED",
    "DELETION_PENDING",
)
EVENT_SOURCES = (
    "CANDIDATE_VOICE",
    "COUNTERQ_VOICE",
    "NATIVE_EDITOR",
    "NATIVE_RUNNER",
    "BROWSER_EXTENSION",
    "INTERVIEW_ORCHESTRATOR",
    "SYSTEM",
)
EVENT_TYPES = (
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
)
TRANSCRIPT_SPEAKERS = ("CANDIDATE", "COUNTERQ")
DELIVERY_STATES = ("STARTED", "DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED", "CANCELLED")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_auth_provider", sa.String(length=64), nullable=False),
        sa.Column("external_auth_subject", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "external_auth_provider",
            "external_auth_subject",
            name="uq_users_external_auth_provider",
        ),
    )

    op.create_table(
        "ai_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("prompt_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "configuration_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("code_revision", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_key", "version", name="uq_ai_policy_versions_key_version"),
    )

    op.create_table(
        "problems",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=256), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_type", "slug", name="uq_problems_source_type_slug"),
    )

    op.create_table(
        "problem_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("problem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "constraints_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "examples_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "io_schema_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("problem_id", "version", name="uq_problem_versions_problem_version"),
        sa.UniqueConstraint("problem_id", "content_hash", name="uq_problem_versions_problem_hash"),
    )

    op.create_table(
        "interview_pack_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("problem_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("preparation_policy_key", sa.String(length=128), nullable=True),
        sa.Column("ai_policy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pack_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["ai_policy_version_id"],
            ["ai_policy_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["problem_version_id"],
            ["problem_versions.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "interview_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=64), nullable=False),
        sa.Column("configured_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("problem_source", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("mode", INTERVIEW_MODES),
            name="ck_interview_configurations_interview_configurations_mode",
        ),
        sa.CheckConstraint(
            _in_values("level", INTERVIEW_LEVELS),
            name="ck_interview_configurations_interview_configurations_level",
        ),
        sa.CheckConstraint(
            "configured_duration_seconds > 0",
            name="ck_interview_configurations_configured_duration_positive",
        ),
    )

    op.create_table(
        "interview_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "interview_configuration_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("problem_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "interview_pack_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("current_stage", sa.String(length=64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_server_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("current_stage", INTERVIEW_STAGES),
            name="ck_interview_sessions_current_stage",
        ),
        sa.CheckConstraint(
            _in_values("status", INTERVIEW_SESSION_STATUSES),
            name="ck_interview_sessions_status",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_interview_sessions_state_version_nonnegative",
        ),
        sa.CheckConstraint(
            "last_server_sequence >= 0",
            name="ck_interview_sessions_last_server_sequence_nonnegative",
        ),
        sa.CheckConstraint(
            "deadline_at > started_at",
            name="ck_interview_sessions_deadline_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["interview_configuration_id"],
            ["interview_configurations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["interview_pack_version_id"],
            ["interview_pack_versions.id"],
        ),
        sa.ForeignKeyConstraint(["problem_version_id"], ["problem_versions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "interview_configuration_id",
            name="uq_interview_sessions_configuration",
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_interview_sessions_id_user"),
    )
    op.create_index(
        "ix_interview_sessions_user_status",
        "interview_sessions",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_interview_sessions_user_completed_at",
        "interview_sessions",
        ["user_id", sa.text("completed_at DESC")],
    )

    op.create_table(
        "session_budgets",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("max_probes", sa.Integer(), nullable=False),
        sa.Column("max_deep_reasoning_calls", sa.Integer(), nullable=False),
        sa.Column("max_strong_reasoning_calls", sa.Integer(), nullable=False),
        sa.Column("max_vision_calls", sa.Integer(), nullable=False),
        sa.Column("soft_monetary_budget", sa.Numeric(12, 4), nullable=False),
        sa.Column("hard_monetary_budget", sa.Numeric(12, 4), nullable=False),
        sa.Column("realtime_reserved_budget", sa.Numeric(12, 4), nullable=False),
        sa.Column("probes_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "deep_reasoning_used",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "strong_reasoning_used", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("vision_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "estimated_cost",
            sa.Numeric(12, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "max_duration_seconds > 0",
            name="ck_session_budgets_max_duration_positive",
        ),
        sa.CheckConstraint(
            "max_probes >= 0",
            name="ck_session_budgets_max_probes_nonnegative",
        ),
        sa.CheckConstraint(
            "max_deep_reasoning_calls >= 0",
            name="ck_session_budgets_max_deep_reasoning_nonnegative",
        ),
        sa.CheckConstraint(
            "max_strong_reasoning_calls >= 0",
            name="ck_session_budgets_max_strong_reasoning_nonnegative",
        ),
        sa.CheckConstraint(
            "max_vision_calls >= 0",
            name="ck_session_budgets_max_vision_nonnegative",
        ),
        sa.CheckConstraint(
            "probes_used >= 0",
            name="ck_session_budgets_probes_used_nonnegative",
        ),
        sa.CheckConstraint(
            "deep_reasoning_used >= 0",
            name="ck_session_budgets_deep_reasoning_used_nonnegative",
        ),
        sa.CheckConstraint(
            "strong_reasoning_used >= 0",
            name="ck_session_budgets_strong_reasoning_used_nonnegative",
        ),
        sa.CheckConstraint(
            "vision_used >= 0",
            name="ck_session_budgets_vision_used_nonnegative",
        ),
        sa.CheckConstraint(
            "soft_monetary_budget >= 0",
            name="ck_session_budgets_soft_monetary_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "hard_monetary_budget >= 0",
            name="ck_session_budgets_hard_monetary_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "realtime_reserved_budget >= 0",
            name="ck_session_budgets_realtime_reserved_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost >= 0", name="ck_session_budgets_estimated_cost_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "interview_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_instance_id", sa.String(length=128), nullable=True),
        sa.Column("client_sequence", sa.BigInteger(), nullable=True),
        sa.Column("server_sequence", sa.BigInteger(), nullable=False),
        sa.Column("interview_state_version", sa.Integer(), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("event_type", EVENT_TYPES), name="ck_interview_events_event_type"
        ),
        sa.CheckConstraint(
            _in_values("source", EVENT_SOURCES),
            name="ck_interview_events_source",
        ),
        sa.CheckConstraint(
            "server_sequence > 0",
            name="ck_interview_events_server_sequence_positive",
        ),
        sa.CheckConstraint(
            "interview_state_version >= 0",
            name="ck_interview_events_interview_state_version_nonnegative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "user_id"],
            ["interview_sessions.id", "interview_sessions.user_id"],
            name="fk_interview_events_session_user",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "server_sequence",
            name="uq_interview_events_session_server_sequence",
        ),
    )
    op.create_index(
        "ix_interview_events_session_server_sequence_desc",
        "interview_events",
        ["interview_session_id", sa.text("server_sequence DESC")],
    )
    op.create_index(
        "uq_interview_events_session_idempotency_key",
        "interview_events",
        ["interview_session_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interview_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("speaker", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("provider_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("interview_stage", sa.String(length=64), nullable=False),
        sa.Column("interview_state_version", sa.Integer(), nullable=False),
        sa.Column("delivery_state", sa.String(length=64), nullable=True),
        sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_segment_id", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("speaker", TRANSCRIPT_SPEAKERS), name="ck_transcript_segments_speaker"
        ),
        sa.CheckConstraint(
            _in_values("interview_stage", INTERVIEW_STAGES),
            name="ck_transcript_segments_interview_stage",
        ),
        sa.CheckConstraint(
            f"delivery_state IS NULL OR {_in_values('delivery_state', DELIVERY_STATES)}",
            name="ck_transcript_segments_delivery_state",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_transcript_segments_sequence_positive"),
        sa.CheckConstraint(
            "interview_state_version >= 0",
            name="ck_transcript_segments_interview_state_version_nonnegative",
        ),
        sa.CheckConstraint(
            "provider_confidence IS NULL OR "
            "(provider_confidence >= 0 AND provider_confidence <= 1)",
            name="ck_transcript_segments_provider_confidence_unit_interval",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_transcript_segments_ended_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["interview_event_id"],
            ["interview_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("interview_event_id", name="uq_transcript_segments_event"),
        sa.UniqueConstraint(
            "interview_session_id",
            "sequence",
            name="uq_transcript_segments_session_sequence",
        ),
    )
    op.create_index(
        "ix_transcript_segments_session_sequence",
        "transcript_segments",
        ["interview_session_id", "sequence"],
    )

    op.create_table(
        "code_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("language", sa.String(length=64), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_from_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("version_number > 0", name="ck_code_snapshots_version_number_positive"),
        sa.ForeignKeyConstraint(
            ["created_from_event_id"],
            ["interview_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["parent_snapshot_id"], ["code_snapshots.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "interview_session_id",
            "version_number",
            name="uq_code_snapshots_session_version",
        ),
        sa.UniqueConstraint("created_from_event_id", name="uq_code_snapshots_created_from_event"),
    )
    op.create_index(
        "ix_code_snapshots_session_version_desc",
        "code_snapshots",
        ["interview_session_id", sa.text("version_number DESC")],
    )

    op.create_foreign_key(
        "fk_interview_events_code_snapshot_id_code_snapshots",
        "interview_events",
        "code_snapshots",
        ["code_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_interview_events_code_snapshot_id_code_snapshots",
        "interview_events",
        type_="foreignkey",
    )
    op.drop_index("ix_code_snapshots_session_version_desc", table_name="code_snapshots")
    op.drop_table("code_snapshots")
    op.drop_index("ix_transcript_segments_session_sequence", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index("uq_interview_events_session_idempotency_key", table_name="interview_events")
    op.drop_index(
        "ix_interview_events_session_server_sequence_desc",
        table_name="interview_events",
    )
    op.drop_table("interview_events")
    op.drop_table("session_budgets")
    op.drop_index("ix_interview_sessions_user_completed_at", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_user_status", table_name="interview_sessions")
    op.drop_table("interview_sessions")
    op.drop_table("interview_configurations")
    op.drop_table("interview_pack_versions")
    op.drop_table("problem_versions")
    op.drop_table("problems")
    op.drop_table("ai_policy_versions")
    op.drop_table("users")

"""stage 3a execution runs

Revision ID: 202608260107
Revises: 202608230106
"""
# ruff: noqa: E501

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202608260107"
down_revision = "202608230106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("problem_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(64), nullable=False),
        sa.Column("runtime_version", sa.String(128)),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("execution_provider", sa.String(64), nullable=False),
        sa.Column("provider_run_id", sa.String(256)),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("compiler_output", sa.Text(), nullable=False, server_default=""),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "output_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("memory_bytes", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'COMPILE_ERROR', 'RUNTIME_ERROR', 'TIMED_OUT', 'OUTPUT_LIMIT_EXCEEDED', 'PROVIDER_ERROR')",
            name="status",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        sa.CheckConstraint("memory_bytes IS NULL OR memory_bytes >= 0", name="memory_nonnegative"),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "run_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_execution_runs_session_run_event",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_execution_runs_session_code_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["problem_version_id"], ["problem_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "interview_session_id", "run_event_id", name="uq_execution_runs_session_run_event"
        ),
        sa.UniqueConstraint(
            "interview_session_id", "idempotency_key", name="uq_execution_runs_session_idempotency"
        ),
    )
    op.create_table(
        "test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_identifier", sa.String(128), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_output", sa.Text()),
        sa.Column("actual_output", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("failure_classification", sa.String(64)),
        sa.CheckConstraint("status IN ('PASSED', 'FAILED', 'NOT_RUN')", name="status"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "execution_run_id", "test_identifier", name="uq_test_results_run_identifier"
        ),
    )


def downgrade() -> None:
    op.drop_table("test_results")
    op.drop_table("execution_runs")

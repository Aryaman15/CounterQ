"""Canonical facts produced by isolated candidate-code execution."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import EXECUTION_RUN_STATUSES, TEST_RESULT_STATUSES
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.interviews.models import InterviewSession
    from app.observation.models import CodeSnapshot, InterviewEvent
    from app.problems.models import ProblemVersion


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interview_session_id", "run_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_execution_runs_session_run_event",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_execution_runs_session_code_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_in_values("status", EXECUTION_RUN_STATUSES), name="status"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        CheckConstraint("memory_bytes IS NULL OR memory_bytes >= 0", name="memory_nonnegative"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        UniqueConstraint(
            "interview_session_id", "run_event_id", name="uq_execution_runs_session_run_event"
        ),
        UniqueConstraint(
            "interview_session_id", "idempotency_key", name="uq_execution_runs_session_idempotency"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    code_snapshot_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    problem_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("problem_versions.id", ondelete="RESTRICT"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_version: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_run_id: Mapped[str | None] = mapped_column(String(256))
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    stderr: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    compiler_output: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    output_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    interview_session: Mapped[InterviewSession] = relationship()
    run_event: Mapped[InterviewEvent] = relationship(foreign_keys=[run_event_id])
    code_snapshot: Mapped[CodeSnapshot] = relationship(foreign_keys=[code_snapshot_id])
    problem_version: Mapped[ProblemVersion] = relationship()
    test_results: Mapped[list[TestResult]] = relationship(
        back_populates="execution_run", cascade="all, delete-orphan", passive_deletes=True
    )


class TestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (
        CheckConstraint(_in_values("status", TEST_RESULT_STATUSES), name="status"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        UniqueConstraint(
            "execution_run_id", "test_identifier", name="uq_test_results_run_identifier"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    execution_run_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    test_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    input_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expected_output: Mapped[str | None] = mapped_column(Text)
    actual_output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    failure_classification: Mapped[str | None] = mapped_column(String(64))

    execution_run: Mapped[ExecutionRun] = relationship(back_populates="test_results")

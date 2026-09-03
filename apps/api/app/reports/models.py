"""Versioned, rebuildable Session Report projection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
from app.db.constants import REPORT_VALIDATION_STATUSES, SESSION_REPORT_STATUSES
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.ai_gateway.models import AIInvocation, AIPolicyVersion
    from app.interviews.models import InterviewSession


class SessionReport(Base):
    __tablename__ = "session_reports"
    __table_args__ = (
        CheckConstraint("report_version > 0", name="report_version_positive"),
        CheckConstraint(_in_values("status", SESSION_REPORT_STATUSES), name="status"),
        CheckConstraint(
            _in_values("validation_status", REPORT_VALIDATION_STATUSES),
            name="validation_status",
        ),
        CheckConstraint("source_watermark >= 0", name="source_watermark_nonnegative"),
        CheckConstraint("source_identity ~ '^sha256:[0-9a-f]{64}$'", name="source_identity_format"),
        CheckConstraint("NOT is_current OR status = 'READY'", name="current_is_ready"),
        CheckConstraint(
            "status <> 'READY' OR (structured_report_json IS NOT NULL "
            "AND generation_policy_version_id IS NOT NULL "
            "AND generation_ai_invocation_id IS NOT NULL "
            "AND generated_at IS NOT NULL AND validation_status = 'PASSED')",
            name="ready_is_complete",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "generation_ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_session_reports_session_ai_invocation",
        ),
        UniqueConstraint(
            "interview_session_id", "report_version", name="uq_session_reports_session_version"
        ),
        Index(
            "uq_session_reports_one_current",
            "interview_session_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_session_reports_session_created", "interview_session_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_request_key: Mapped[str] = mapped_column(String(320), nullable=False)
    generation_policy_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_policy_versions.id")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    structured_report_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    rendered_markdown: Mapped[str | None] = mapped_column(Text)
    generation_ai_invocation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_identity: Mapped[str] = mapped_column(String(71), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_failure_category: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    interview_session: Mapped[InterviewSession] = relationship()
    generation_policy_version: Mapped[AIPolicyVersion | None] = relationship(
        foreign_keys=[generation_policy_version_id]
    )
    generation_ai_invocation: Mapped[AIInvocation | None] = relationship(
        foreign_keys=[generation_ai_invocation_id]
    )

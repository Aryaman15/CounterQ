from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    INTERVIEW_LEVELS,
    INTERVIEW_MODES,
    INTERVIEW_SESSION_STATUSES,
    INTERVIEW_STAGES,
)
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.auth.models import User
    from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
    from app.problems.models import InterviewPackVersion, ProblemVersion


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


class InterviewConfiguration(Base):
    __tablename__ = "interview_configurations"
    __table_args__ = (
        CheckConstraint(
            _in_values("mode", INTERVIEW_MODES),
            name="interview_configurations_mode",
        ),
        CheckConstraint(
            _in_values("level", INTERVIEW_LEVELS), name="interview_configurations_level"
        ),
        CheckConstraint("configured_duration_seconds > 0", name="configured_duration_positive"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    configured_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    problem_source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession | None] = relationship(
        back_populates="configuration",
        uselist=False,
    )


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        CheckConstraint(_in_values("current_stage", INTERVIEW_STAGES), name="current_stage"),
        CheckConstraint(_in_values("status", INTERVIEW_SESSION_STATUSES), name="status"),
        CheckConstraint("state_version >= 0", name="state_version_nonnegative"),
        CheckConstraint("last_server_sequence >= 0", name="last_server_sequence_nonnegative"),
        CheckConstraint("deadline_at > started_at", name="deadline_after_started"),
        UniqueConstraint("interview_configuration_id", name="uq_interview_sessions_configuration"),
        UniqueConstraint("id", "user_id", name="uq_interview_sessions_id_user"),
        Index("ix_interview_sessions_user_status", "user_id", "status"),
        Index("ix_interview_sessions_user_completed_at", "user_id", text("completed_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    interview_configuration_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_configurations.id"),
        nullable=False,
    )
    problem_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("problem_versions.id"),
        nullable=False,
    )
    interview_pack_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_pack_versions.id"),
        nullable=False,
    )
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_server_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    user: Mapped[User] = relationship(back_populates="interview_sessions")
    configuration: Mapped[InterviewConfiguration] = relationship(back_populates="interview_session")
    problem_version: Mapped[ProblemVersion] = relationship(back_populates="interview_sessions")
    interview_pack_version: Mapped[InterviewPackVersion] = relationship(
        back_populates="interview_sessions",
    )
    budget: Mapped[SessionBudget | None] = relationship(
        back_populates="interview_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    events: Mapped[list[InterviewEvent]] = relationship(
        back_populates="interview_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="InterviewEvent.interview_session_id",
    )
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="interview_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    code_snapshots: Mapped[list[CodeSnapshot]] = relationship(
        back_populates="interview_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="CodeSnapshot.interview_session_id",
    )


class SessionBudget(Base):
    __tablename__ = "session_budgets"
    __table_args__ = (
        CheckConstraint("max_duration_seconds > 0", name="max_duration_positive"),
        CheckConstraint("max_probes >= 0", name="max_probes_nonnegative"),
        CheckConstraint("max_deep_reasoning_calls >= 0", name="max_deep_reasoning_nonnegative"),
        CheckConstraint("max_strong_reasoning_calls >= 0", name="max_strong_reasoning_nonnegative"),
        CheckConstraint("max_vision_calls >= 0", name="max_vision_nonnegative"),
        CheckConstraint("probes_used >= 0", name="probes_used_nonnegative"),
        CheckConstraint("deep_reasoning_used >= 0", name="deep_reasoning_used_nonnegative"),
        CheckConstraint("strong_reasoning_used >= 0", name="strong_reasoning_used_nonnegative"),
        CheckConstraint("vision_used >= 0", name="vision_used_nonnegative"),
        CheckConstraint("soft_monetary_budget >= 0", name="soft_monetary_budget_nonnegative"),
        CheckConstraint("hard_monetary_budget >= 0", name="hard_monetary_budget_nonnegative"),
        CheckConstraint(
            "realtime_reserved_budget >= 0",
            name="realtime_reserved_budget_nonnegative",
        ),
        CheckConstraint("estimated_cost >= 0", name="estimated_cost_nonnegative"),
    )

    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_probes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_deep_reasoning_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_strong_reasoning_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_vision_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_monetary_budget: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    hard_monetary_budget: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    realtime_reserved_budget: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    probes_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deep_reasoning_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strong_reasoning_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vision_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship(back_populates="budget")

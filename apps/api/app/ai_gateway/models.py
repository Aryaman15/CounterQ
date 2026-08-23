from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import AI_INVOCATION_STATUSES
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.auth.models import User
    from app.interviews.models import InterviewSession


class AIPolicyVersion(Base):
    __tablename__ = "ai_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_key", "version", name="uq_ai_policy_versions_key_version"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    policy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str | None] = mapped_column(String(128))
    configuration_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    code_revision: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIInvocation(Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        CheckConstraint(_in_values("status", AI_INVOCATION_STATUSES), name="status"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_nonnegative"
        ),
        CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name="cached_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_nonnegative"
        ),
        CheckConstraint(
            "audio_input_units IS NULL OR audio_input_units >= 0", name="audio_input_nonnegative"
        ),
        CheckConstraint(
            "audio_output_units IS NULL OR audio_output_units >= 0", name="audio_output_nonnegative"
        ),
        CheckConstraint("image_units IS NULL OR image_units >= 0", name="image_units_nonnegative"),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0", name="estimated_cost_nonnegative"
        ),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        UniqueConstraint("interview_session_id", "id", name="uq_ai_invocations_session_id"),
        Index("ix_ai_invocations_session_purpose", "interview_session_id", "purpose"),
        Index(
            "ix_ai_invocations_provider_model_started", "provider", "model", text("started_at DESC")
        ),
        Index("ix_ai_invocations_purpose_started", "purpose", text("started_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    interview_session_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="SET NULL"),
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_model_version: Mapped[str | None] = mapped_column(String(128))
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(96), nullable=False)
    ai_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_policy_versions.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    audio_input_units: Mapped[int | None] = mapped_column(Integer)
    audio_output_units: Mapped[int | None] = mapped_column(Integer)
    image_units: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    error_class: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    user: Mapped[User | None] = relationship()
    interview_session: Mapped[InterviewSession | None] = relationship()
    ai_policy_version: Mapped[AIPolicyVersion] = relationship()

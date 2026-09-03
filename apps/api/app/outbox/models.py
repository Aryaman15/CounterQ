"""Durable intentions for Stage 6B eventual post-session work."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import OUTBOX_EVENT_STATUSES, OUTBOX_EVENT_TYPES
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.interviews.models import InterviewSession


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(_in_values("event_type", OUTBOX_EVENT_TYPES), name="event_type"),
        CheckConstraint(_in_values("status", OUTBOX_EVENT_STATUSES), name="status"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "next_retry_at IS NULL OR next_retry_at >= created_at",
            name="retry_not_before_creation",
        ),
        Index("ix_outbox_events_dispatch", "status", "available_at", "next_retry_at"),
        Index("ix_outbox_events_session_created", "interview_session_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    aggregate_type: Mapped[str] = mapped_column(String(96), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    deduplication_key: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    source_watermark: Mapped[int | None] = mapped_column(BigInteger)

    interview_session: Mapped[InterviewSession] = relationship()

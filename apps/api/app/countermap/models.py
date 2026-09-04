"""Versioned, rebuildable CounterMap projection persistence."""

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
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import COUNTERMAP_PROJECTION_STATUSES
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.interviews.models import InterviewSession


class CounterMapProjection(Base):
    __tablename__ = "countermap_projections"
    __table_args__ = (
        CheckConstraint("projection_version > 0", name="projection_version_positive"),
        CheckConstraint(
            _in_values("status", COUNTERMAP_PROJECTION_STATUSES),
            name="status",
        ),
        CheckConstraint("source_watermark >= 0", name="source_watermark_nonnegative"),
        CheckConstraint(
            "source_identity ~ '^sha256:[0-9a-f]{64}$'",
            name="source_identity_format",
        ),
        CheckConstraint("NOT is_current OR status = 'READY'", name="current_is_ready"),
        CheckConstraint(
            "status <> 'READY' OR (graph_json IS NOT NULL AND generated_at IS NOT NULL)",
            name="ready_is_complete",
        ),
        UniqueConstraint(
            "interview_session_id",
            "projection_version",
            name="uq_countermap_projections_session_version",
        ),
        UniqueConstraint(
            "interview_session_id",
            "generation_request_key",
            name="uq_countermap_projections_session_request",
        ),
        Index(
            "uq_countermap_projections_one_current",
            "interview_session_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_countermap_projections_session_created",
            "interview_session_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    generation_request_key: Mapped[str] = mapped_column(String(320), nullable=False)
    source_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_identity: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    graph_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    last_failure_category: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    interview_session: Mapped[InterviewSession] = relationship()

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
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    DELIVERY_STATES,
    EVENT_SOURCES,
    EVENT_TYPES,
    INTERVIEW_STAGES,
    TRANSCRIPT_SPEAKERS,
)
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.auth.models import User
    from app.interviews.models import InterviewSession


class InterviewEvent(Base):
    __tablename__ = "interview_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interview_session_id", "user_id"],
            ["interview_sessions.id", "interview_sessions.user_id"],
            name="fk_interview_events_session_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in_values("event_type", EVENT_TYPES), name="event_type"),
        CheckConstraint(_in_values("source", EVENT_SOURCES), name="source"),
        CheckConstraint("server_sequence > 0", name="server_sequence_positive"),
        CheckConstraint("interview_state_version >= 0", name="interview_state_version_nonnegative"),
        UniqueConstraint(
            "interview_session_id",
            "server_sequence",
            name="uq_interview_events_session_server_sequence",
        ),
        Index(
            "ix_interview_events_session_server_sequence_desc",
            "interview_session_id",
            sql_text("server_sequence DESC"),
        ),
        Index(
            "uq_interview_events_session_idempotency_key",
            "interview_session_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sql_text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_instance_id: Mapped[str | None] = mapped_column(String(128))
    client_sequence: Mapped[int | None] = mapped_column(BigInteger)
    server_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    interview_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    correlation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    code_snapshot_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("code_snapshots.id", ondelete="SET NULL"),
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    provenance: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship(
        back_populates="events",
        foreign_keys=[interview_session_id],
    )
    user: Mapped[User] = relationship()
    code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        back_populates="referencing_events",
        foreign_keys=[code_snapshot_id],
    )
    transcript_segment: Mapped[TranscriptSegment | None] = relationship(
        back_populates="interview_event",
        uselist=False,
    )
    created_code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        back_populates="created_from_event",
        foreign_keys="CodeSnapshot.created_from_event_id",
        uselist=False,
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        CheckConstraint(_in_values("speaker", TRANSCRIPT_SPEAKERS), name="speaker"),
        CheckConstraint(_in_values("interview_stage", INTERVIEW_STAGES), name="interview_stage"),
        CheckConstraint(
            f"delivery_state IS NULL OR {_in_values('delivery_state', DELIVERY_STATES)}",
            name="delivery_state",
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("interview_state_version >= 0", name="interview_state_version_nonnegative"),
        CheckConstraint(
            "provider_confidence IS NULL OR "
            "(provider_confidence >= 0 AND provider_confidence <= 1)",
            name="provider_confidence_unit_interval",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        UniqueConstraint("interview_event_id", name="uq_transcript_segments_event"),
        UniqueConstraint(
            "interview_session_id",
            "sequence",
            name="uq_transcript_segments_session_sequence",
        ),
        Index("ix_transcript_segments_session_sequence", "interview_session_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    interview_event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    speaker: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    provider_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    interview_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    interview_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_state: Mapped[str | None] = mapped_column(String(64))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_segment_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship(back_populates="transcript_segments")
    interview_event: Mapped[InterviewEvent] = relationship(back_populates="transcript_segment")


class CodeSnapshot(Base):
    __tablename__ = "code_snapshots"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="version_number_positive"),
        UniqueConstraint(
            "interview_session_id",
            "version_number",
            name="uq_code_snapshots_session_version",
        ),
        UniqueConstraint("created_from_event_id", name="uq_code_snapshots_created_from_event"),
        Index(
            "ix_code_snapshots_session_version_desc",
            "interview_session_id",
            sql_text("version_number DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_snapshot_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("code_snapshots.id", ondelete="SET NULL"),
    )
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_from_event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship(
        back_populates="code_snapshots",
        foreign_keys=[interview_session_id],
    )
    parent_snapshot: Mapped[CodeSnapshot | None] = relationship(
        remote_side=[id],
        foreign_keys=[parent_snapshot_id],
    )
    created_from_event: Mapped[InterviewEvent] = relationship(
        back_populates="created_code_snapshot",
        foreign_keys=[created_from_event_id],
    )
    referencing_events: Mapped[list[InterviewEvent]] = relationship(
        back_populates="code_snapshot",
        foreign_keys="InterviewEvent.code_snapshot_id",
    )

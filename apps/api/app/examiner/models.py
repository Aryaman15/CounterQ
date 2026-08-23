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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    CLAIM_ORIGIN_KINDS,
    CLAIM_STATUSES,
    CLAIM_TYPES,
    EXAMINER_ACTIONS,
    EXAMINER_DECISION_STATUSES,
    POLICY_GATE_OUTCOMES,
    PROBE_STRATEGIES,
)
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.ai_gateway.models import AIInvocation, AIPolicyVersion
    from app.interviews.models import InterviewSession
    from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment


class CandidateClaim(Base):
    __tablename__ = "candidate_claims"
    __table_args__ = (
        CheckConstraint(_in_values("origin_kind", CLAIM_ORIGIN_KINDS), name="origin_kind"),
        CheckConstraint(_in_values("claim_type", CLAIM_TYPES), name="claim_type"),
        CheckConstraint(_in_values("status", CLAIM_STATUSES), name="status"),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="extraction_confidence_unit_interval",
        ),
        CheckConstraint(
            "source_transcript_segment_id IS NOT NULL OR "
            "source_event_id IS NOT NULL OR "
            "source_code_snapshot_id IS NOT NULL OR "
            "source_code_diff_id IS NOT NULL",
            name="has_factual_source",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_transcript_segment_id"],
            ["transcript_segments.interview_session_id", "transcript_segments.id"],
            name="fk_candidate_claims_session_transcript",
            ondelete="SET NULL (source_transcript_segment_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_candidate_claims_session_event",
            ondelete="SET NULL (source_event_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_candidate_claims_session_code_snapshot",
            ondelete="SET NULL (source_code_snapshot_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_code_diff_id"],
            ["code_diffs.interview_session_id", "code_diffs.id"],
            name="fk_candidate_claims_session_code_diff",
            ondelete="SET NULL (source_code_diff_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_candidate_claims_session_ai_invocation",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_candidate_claims_session_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    origin_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_transcript_segment_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_event_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_code_snapshot_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_code_diff_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    verbatim_excerpt: Mapped[str | None] = mapped_column(Text)
    normalized_claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_invocation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_invocations.id"),
        nullable=False,
    )
    ai_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_policy_versions.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship()
    source_transcript_segment: Mapped[TranscriptSegment | None] = relationship(
        foreign_keys=[source_transcript_segment_id],
    )
    source_event: Mapped[InterviewEvent | None] = relationship(foreign_keys=[source_event_id])
    source_code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        foreign_keys=[source_code_snapshot_id],
    )
    source_code_diff: Mapped[CodeDiff | None] = relationship(foreign_keys=[source_code_diff_id])
    ai_invocation: Mapped[AIInvocation] = relationship(foreign_keys=[ai_invocation_id])
    ai_policy_version: Mapped[AIPolicyVersion] = relationship()


class ExaminerDecision(Base):
    __tablename__ = "examiner_decisions"
    __table_args__ = (
        CheckConstraint(_in_values("action", EXAMINER_ACTIONS), name="action"),
        CheckConstraint(
            "proposed_probe_strategy IS NULL OR "
            f"{_in_values('proposed_probe_strategy', PROBE_STRATEGIES)}",
            name="proposed_probe_strategy",
        ),
        CheckConstraint(
            "(action = 'PROBE' AND proposed_probe_strategy IS NOT NULL) OR "
            "(action <> 'PROBE' AND proposed_probe_strategy IS NULL)",
            name="probe_strategy_matches_action",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint("priority IS NULL OR priority >= 0", name="priority_nonnegative"),
        CheckConstraint("urgency IS NULL OR urgency >= 0", name="urgency_nonnegative"),
        CheckConstraint("source_event_watermark >= 0", name="source_event_watermark_nonnegative"),
        CheckConstraint("source_state_version >= 0", name="source_state_version_nonnegative"),
        CheckConstraint(
            "policy_gate_outcome IS NULL OR "
            f"{_in_values('policy_gate_outcome', POLICY_GATE_OUTCOMES)}",
            name="policy_gate_outcome",
        ),
        CheckConstraint(_in_values("status", EXAMINER_DECISION_STATUSES), name="status"),
        ForeignKeyConstraint(
            ["interview_session_id", "target_claim_id"],
            ["candidate_claims.interview_session_id", "candidate_claims.id"],
            name="fk_examiner_decisions_session_claim",
            ondelete="SET NULL (target_claim_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "target_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_examiner_decisions_session_event",
            ondelete="SET NULL (target_event_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "target_code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_examiner_decisions_session_code_snapshot",
            ondelete="SET NULL (target_code_snapshot_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_examiner_decisions_session_ai_invocation",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_examiner_decisions_session_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target_claim_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_event_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_code_snapshot_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    proposed_probe_strategy: Mapped[str | None] = mapped_column(String(64))
    technical_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    priority: Mapped[int | None] = mapped_column(Integer)
    urgency: Mapped[int | None] = mapped_column(Integer)
    source_event_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_policy: Mapped[str | None] = mapped_column(String(128))
    policy_gate_outcome: Mapped[str | None] = mapped_column(String(64))
    policy_gate_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_invocation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_invocations.id"),
        nullable=False,
    )
    ai_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_policy_versions.id"),
        nullable=False,
    )

    interview_session: Mapped[InterviewSession] = relationship()
    target_claim: Mapped[CandidateClaim | None] = relationship(foreign_keys=[target_claim_id])
    target_event: Mapped[InterviewEvent | None] = relationship(foreign_keys=[target_event_id])
    target_code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        foreign_keys=[target_code_snapshot_id],
    )
    ai_invocation: Mapped[AIInvocation] = relationship(foreign_keys=[ai_invocation_id])
    ai_policy_version: Mapped[AIPolicyVersion] = relationship()

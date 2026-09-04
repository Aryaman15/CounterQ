from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    ASSESSMENT_DIMENSIONS,
    ASSESSMENT_STATUSES,
    BREAKPOINT_EVIDENCE_RELATIONSHIPS,
    BREAKPOINT_STATUSES,
    EVIDENCE_INDEPENDENCE_LEVELS,
    EVIDENCE_POLARITIES,
    EVIDENCE_SOURCE_ROLES,
    EVIDENCE_STRENGTHS,
    EVIDENCE_VALIDATION_STATUSES,
)
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.ai_gateway.models import AIInvocation, AIPolicyVersion
    from app.auth.models import User
    from app.examiner.models import CandidateClaim
    from app.interviews.models import CandidateResponse, InterviewSession
    from app.observation.models import CodeSnapshot, InterviewEvent
    from app.problems.models import Concept

orm_relationship = relationship


class SkillDimension(Base):
    __tablename__ = "skill_dimensions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    canonical_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AssessmentUnitEvaluation(Base):
    """Operational proof that deterministic admission finished for one stable unit."""

    __tablename__ = "assessment_unit_evaluations"
    __table_args__ = (
        CheckConstraint(
            "unit_key ~ '^sha256:[0-9a-f]{64}$'",
            name="unit_key_format",
        ),
        CheckConstraint("length(btrim(unit_kind)) > 0", name="unit_kind_nonempty"),
        CheckConstraint("finding_count >= 0", name="finding_count_nonnegative"),
        ForeignKeyConstraint(
            ["interview_session_id", "successful_ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_assessment_unit_evaluations_session_ai_invocation",
        ),
        UniqueConstraint(
            "interview_session_id",
            "unit_key",
            "evaluator_policy_version_id",
            name="uq_assessment_unit_evaluations_session_unit_policy",
        ),
        Index(
            "ix_assessment_unit_evaluations_session_completed_at",
            "interview_session_id",
            "completed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    unit_key: Mapped[str] = mapped_column(String(71), nullable=False)
    unit_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_policy_versions.id"), nullable=False
    )
    successful_ai_invocation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_invocations.id"), nullable=False
    )
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    interview_session: Mapped[InterviewSession] = relationship(
        foreign_keys=[interview_session_id]
    )
    evaluator_policy_version: Mapped[AIPolicyVersion] = relationship(
        foreign_keys=[evaluator_policy_version_id]
    )
    successful_ai_invocation: Mapped[AIInvocation] = relationship(
        foreign_keys=[successful_ai_invocation_id]
    )


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(
            _in_values("assessment_dimension", ASSESSMENT_DIMENSIONS), name="dimension"
        ),
        CheckConstraint(_in_values("polarity", EVIDENCE_POLARITIES), name="polarity"),
        CheckConstraint(_in_values("status", ASSESSMENT_STATUSES), name="status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit_interval"),
        CheckConstraint("length(btrim(rationale)) > 0", name="rationale_nonempty"),
        CheckConstraint(
            "evaluation_key IS NULL OR evaluation_key ~ '^sha256:[0-9a-f]{64}$'",
            name="evaluation_key_format",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "candidate_response_id"],
            ["candidate_responses.interview_session_id", "candidate_responses.id"],
            name="fk_assessments_session_response",
            ondelete="SET NULL (candidate_response_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "target_claim_id"],
            ["candidate_claims.interview_session_id", "candidate_claims.id"],
            name="fk_assessments_session_claim",
            ondelete="SET NULL (target_claim_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_assessments_session_code_snapshot",
            ondelete="SET NULL (source_code_snapshot_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_assessments_session_ai_invocation",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_assessments_session_id"),
        Index("ix_assessments_session_created_at", "interview_session_id", "created_at"),
        Index(
            "uq_assessments_session_evaluation_key",
            "interview_session_id",
            "evaluation_key",
            unique=True,
            postgresql_where=text("evaluation_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_response_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("candidate_responses.id", ondelete="SET NULL")
    )
    target_claim_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("candidate_claims.id", ondelete="SET NULL")
    )
    source_code_snapshot_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("code_snapshots.id", ondelete="SET NULL")
    )
    assessment_dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    polarity: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stable Stage 5B idempotency identity. It is derived from immutable factual
    # provenance, evaluator policy, dimension, and canonical targets -- never prose.
    evaluation_key: Mapped[str | None] = mapped_column(String(71))
    ai_invocation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_invocations.id"), nullable=False
    )
    ai_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_policy_versions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    interview_session: Mapped[InterviewSession] = relationship(foreign_keys=[interview_session_id])
    candidate_response: Mapped[CandidateResponse | None] = relationship(
        foreign_keys=[candidate_response_id]
    )
    target_claim: Mapped[CandidateClaim | None] = relationship(foreign_keys=[target_claim_id])
    source_code_snapshot: Mapped[CodeSnapshot | None] = relationship(
        foreign_keys=[source_code_snapshot_id]
    )
    ai_invocation: Mapped[AIInvocation] = relationship(foreign_keys=[ai_invocation_id])
    ai_policy_version: Mapped[AIPolicyVersion] = relationship(foreign_keys=[ai_policy_version_id])
    sources: Mapped[list[AssessmentSource]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="AssessmentSource.assessment_id",
    )


class AssessmentSource(Base):
    __tablename__ = "assessment_sources"
    __table_args__ = (
        CheckConstraint(_in_values("source_role", EVIDENCE_SOURCE_ROLES), name="source_role"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        ForeignKeyConstraint(
            ["interview_session_id", "assessment_id"],
            ["assessments.interview_session_id", "assessments.id"],
            name="fk_assessment_sources_session_assessment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_assessment_sources_session_event",
        ),
        UniqueConstraint("assessment_id", "sequence", name="uq_assessment_sources_sequence"),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), primary_key=True
    )
    interview_event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("interview_events.id"), primary_key=True
    )
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_role: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    assessment: Mapped[Assessment] = relationship(
        back_populates="sources", foreign_keys=[assessment_id]
    )
    interview_event: Mapped[InterviewEvent] = relationship(foreign_keys=[interview_event_id])


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint("length(btrim(evidence_type)) > 0", name="type_nonempty"),
        CheckConstraint(_in_values("polarity", EVIDENCE_POLARITIES), name="polarity"),
        CheckConstraint(_in_values("strength", EVIDENCE_STRENGTHS), name="strength"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit_interval"),
        CheckConstraint("length(btrim(finding)) > 0", name="finding_nonempty"),
        CheckConstraint(
            _in_values("independence_level", EVIDENCE_INDEPENDENCE_LEVELS),
            name="independence_level",
        ),
        CheckConstraint(
            _in_values("validation_status", EVIDENCE_VALIDATION_STATUSES),
            name="validation_status",
        ),
        CheckConstraint(
            "(validation_status = 'INVALIDATED' AND invalidated_at IS NOT NULL "
            "AND invalidation_reason IS NOT NULL AND length(btrim(invalidation_reason)) > 0) OR "
            "(validation_status <> 'INVALIDATED' AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL)",
            name="invalidation_state_consistent",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "originating_assessment_id"],
            ["assessments.interview_session_id", "assessments.id"],
            name="fk_evidence_session_assessment",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_evidence_session_id"),
        Index("ix_evidence_session_created_at", "interview_session_id", "created_at"),
        Index(
            "ix_evidence_session_active",
            "interview_session_id",
            "created_at",
            postgresql_where=text("validation_status = 'VALID' AND invalidated_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    polarity: Mapped[str] = mapped_column(String(32), nullable=False)
    strength: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    independence_level: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    originating_assessment_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assessments.id"), nullable=False
    )
    validation_policy_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_policy_versions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)

    interview_session: Mapped[InterviewSession] = relationship(foreign_keys=[interview_session_id])
    originating_assessment: Mapped[Assessment] = relationship(
        foreign_keys=[originating_assessment_id]
    )
    validation_policy_version: Mapped[AIPolicyVersion] = relationship(
        foreign_keys=[validation_policy_version_id]
    )
    sources: Mapped[list[EvidenceSource]] = relationship(
        back_populates="evidence",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="EvidenceSource.evidence_id",
    )
    concepts: Mapped[list[EvidenceConcept]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan", passive_deletes=True
    )
    skills: Mapped[list[EvidenceSkill]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan", passive_deletes=True
    )


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"
    __table_args__ = (
        CheckConstraint(_in_values("source_role", EVIDENCE_SOURCE_ROLES), name="source_role"),
        ForeignKeyConstraint(
            ["interview_session_id", "evidence_id"],
            ["evidence.interview_session_id", "evidence.id"],
            name="fk_evidence_sources_session_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_evidence_sources_session_event",
        ),
        Index("ix_evidence_sources_evidence", "evidence_id"),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    interview_event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("interview_events.id"), primary_key=True
    )
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_role: Mapped[str] = mapped_column(String(32), nullable=False)

    evidence: Mapped[Evidence] = relationship(back_populates="sources", foreign_keys=[evidence_id])
    interview_event: Mapped[InterviewEvent] = relationship(foreign_keys=[interview_event_id])


class EvidenceConcept(Base):
    __tablename__ = "evidence_concepts"
    __table_args__ = (
        CheckConstraint("relevance > 0 AND relevance <= 1", name="relevance_unit_interval"),
        Index("ix_evidence_concepts_concept_evidence", "concept_id", "evidence_id"),
        Index(
            "uq_evidence_concepts_one_primary",
            "evidence_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), primary_key=True
    )
    relevance: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    evidence: Mapped[Evidence] = relationship(back_populates="concepts")
    concept: Mapped[Concept] = relationship(foreign_keys=[concept_id])


class EvidenceSkill(Base):
    __tablename__ = "evidence_skills"
    __table_args__ = (
        CheckConstraint("relevance > 0 AND relevance <= 1", name="relevance_unit_interval"),
        Index("ix_evidence_skills_skill_evidence", "skill_dimension_id", "evidence_id"),
        Index(
            "uq_evidence_skills_one_primary",
            "evidence_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    skill_dimension_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("skill_dimensions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relevance: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    evidence: Mapped[Evidence] = relationship(back_populates="skills")
    skill_dimension: Mapped[SkillDimension] = relationship(foreign_keys=[skill_dimension_id])


class Breakpoint(Base):
    __tablename__ = "breakpoints"
    __table_args__ = (
        CheckConstraint(_in_values("status", BREAKPOINT_STATUSES), name="status"),
        CheckConstraint("breakpoint_key ~ '^[a-z0-9]+(_[a-z0-9]+)*$'", name="key_normalized"),
        CheckConstraint("length(btrim(severity)) > 0", name="severity_nonempty"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_nonempty"),
        CheckConstraint(
            "(status IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NOT NULL "
            "AND resolution_reason IS NOT NULL AND length(btrim(resolution_reason)) > 0) OR "
            "(status NOT IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NULL "
            "AND resolution_reason IS NULL)",
            name="resolution_state_consistent",
        ),
        ForeignKeyConstraint(
            ["first_detected_session_id", "user_id"],
            ["interview_sessions.id", "interview_sessions.user_id"],
            name="fk_breakpoints_session_user",
        ),
        Index(
            "uq_breakpoints_active_identity",
            "user_id",
            "concept_id",
            "skill_dimension_id",
            "breakpoint_key",
            unique=True,
            postgresql_where=text("status IN ('OPEN', 'RETEST_PENDING', 'IMPROVING')"),
        ),
        Index(
            "ix_breakpoints_user_active",
            "user_id",
            "concept_id",
            postgresql_where=text("status IN ('OPEN', 'RETEST_PENDING', 'IMPROVING')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    skill_dimension_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("skill_dimensions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    breakpoint_key: Mapped[str] = mapped_column(String(256), nullable=False)
    first_detected_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False
    )
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    concept: Mapped[Concept] = relationship(foreign_keys=[concept_id])
    skill_dimension: Mapped[SkillDimension] = relationship(foreign_keys=[skill_dimension_id])
    first_detected_session: Mapped[InterviewSession] = relationship(
        foreign_keys=[first_detected_session_id]
    )
    evidence_links: Mapped[list[BreakpointEvidence]] = relationship(
        back_populates="breakpoint", cascade="all, delete-orphan", passive_deletes=True
    )


class BreakpointEvidence(Base):
    __tablename__ = "breakpoint_evidence"
    __table_args__ = (
        CheckConstraint(
            _in_values("relationship", BREAKPOINT_EVIDENCE_RELATIONSHIPS),
            name="relationship",
        ),
        Index("ix_breakpoint_evidence_evidence", "evidence_id"),
    )

    breakpoint_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("breakpoints.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("evidence.id"), primary_key=True
    )
    relationship: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    breakpoint: Mapped[Breakpoint] = orm_relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = orm_relationship(foreign_keys=[evidence_id])

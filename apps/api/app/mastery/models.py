"""Frozen Stage 8 mastery, transition, and retest persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    MASTERY_CONTRIBUTIONS,
    MASTERY_STATES,
    MASTERY_TARGET_TYPES,
    RETEST_RECOMMENDATION_STATUSES,
)
from app.db.ids import uuid7
from app.interviews.models import _in_values

if TYPE_CHECKING:
    from app.auth.models import User
    from app.evidence.models import Evidence, SkillDimension
    from app.interviews.models import InterviewerPrompt, InterviewSession
    from app.problems.models import Concept


class ConceptMastery(Base):
    __tablename__ = "concept_mastery"
    __table_args__ = (
        CheckConstraint(_in_values("state", MASTERY_STATES), name="state"),
        CheckConstraint("projection_version > 0", name="projection_version_positive"),
        CheckConstraint("supporting_evidence_count >= 0", name="support_count_nonnegative"),
        CheckConstraint("context_diversity >= 0", name="context_diversity_nonnegative"),
        UniqueConstraint("user_id", "concept_id", name="uq_concept_mastery_user_concept"),
        Index("ix_concept_mastery_user_state", "user_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mastery_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supporting_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    context_diversity: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    concept: Mapped[Concept] = relationship(foreign_keys=[concept_id])
    evidence_links: Mapped[list[ConceptMasteryEvidence]] = relationship(
        back_populates="mastery", cascade="all, delete-orphan", passive_deletes=True
    )


class SkillMastery(Base):
    __tablename__ = "skill_mastery"
    __table_args__ = (
        CheckConstraint(_in_values("state", MASTERY_STATES), name="state"),
        CheckConstraint("projection_version > 0", name="projection_version_positive"),
        CheckConstraint("supporting_evidence_count >= 0", name="support_count_nonnegative"),
        CheckConstraint("context_diversity >= 0", name="context_diversity_nonnegative"),
        UniqueConstraint(
            "user_id",
            "skill_dimension_id",
            name="uq_skill_mastery_user_skill_dimension",
        ),
        Index("ix_skill_mastery_user_state", "user_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    skill_dimension_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("skill_dimensions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mastery_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supporting_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    context_diversity: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    skill_dimension: Mapped[SkillDimension] = relationship(foreign_keys=[skill_dimension_id])
    evidence_links: Mapped[list[SkillMasteryEvidence]] = relationship(
        back_populates="mastery", cascade="all, delete-orphan", passive_deletes=True
    )


class ConceptMasteryEvidence(Base):
    __tablename__ = "concept_mastery_evidence"
    __table_args__ = (
        CheckConstraint(
            _in_values("contribution_classification", MASTERY_CONTRIBUTIONS),
            name="contribution_classification",
        ),
        CheckConstraint("length(btrim(context_key)) > 0", name="context_key_nonempty"),
        ForeignKeyConstraint(
            ["user_id", "concept_id"],
            ["concept_mastery.user_id", "concept_mastery.concept_id"],
            name="fk_concept_mastery_evidence_current",
            ondelete="CASCADE",
        ),
        Index("ix_concept_mastery_evidence_evidence", "evidence_id"),
    )

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    concept_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contribution_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    context_key: Mapped[str] = mapped_column(String(320), nullable=False)
    admitted_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    mastery: Mapped[ConceptMastery] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(foreign_keys=[evidence_id])


class SkillMasteryEvidence(Base):
    __tablename__ = "skill_mastery_evidence"
    __table_args__ = (
        CheckConstraint(
            _in_values("contribution_classification", MASTERY_CONTRIBUTIONS),
            name="contribution_classification",
        ),
        CheckConstraint("length(btrim(context_key)) > 0", name="context_key_nonempty"),
        ForeignKeyConstraint(
            ["user_id", "skill_dimension_id"],
            ["skill_mastery.user_id", "skill_mastery.skill_dimension_id"],
            name="fk_skill_mastery_evidence_current",
            ondelete="CASCADE",
        ),
        Index("ix_skill_mastery_evidence_evidence", "evidence_id"),
    )

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    skill_dimension_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contribution_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    context_key: Mapped[str] = mapped_column(String(320), nullable=False)
    admitted_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    mastery: Mapped[SkillMastery] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(foreign_keys=[evidence_id])


class MasteryTransition(Base):
    __tablename__ = "mastery_transitions"
    __table_args__ = (
        CheckConstraint(_in_values("target_type", MASTERY_TARGET_TYPES), name="target_type"),
        CheckConstraint(_in_values("from_state", MASTERY_STATES), name="from_state"),
        CheckConstraint(_in_values("to_state", MASTERY_STATES), name="to_state"),
        CheckConstraint("from_state <> to_state", name="state_changes"),
        CheckConstraint(
            "(target_type = 'CONCEPT' AND concept_id IS NOT NULL "
            "AND skill_dimension_id IS NULL) OR "
            "(target_type = 'SKILL' AND concept_id IS NULL "
            "AND skill_dimension_id IS NOT NULL)",
            name="target_xor",
        ),
        UniqueConstraint("transition_key", name="uq_mastery_transitions_transition_key"),
        Index("ix_mastery_transitions_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    concept_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT")
    )
    skill_dimension_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("skill_dimensions.id", ondelete="RESTRICT")
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    mastery_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    transition_key: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    evidence_links: Mapped[list[MasteryTransitionEvidence]] = relationship(
        back_populates="transition", cascade="all, delete-orphan", passive_deletes=True
    )


class MasteryTransitionEvidence(Base):
    __tablename__ = "mastery_transition_evidence"

    mastery_transition_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("mastery_transitions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )
    transition: Mapped[MasteryTransition] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(foreign_keys=[evidence_id])


class RetestRecommendation(Base):
    __tablename__ = "retest_recommendations"
    __table_args__ = (
        CheckConstraint(_in_values("status", RETEST_RECOMMENDATION_STATUSES), name="status"),
        CheckConstraint(
            "concept_id IS NOT NULL OR skill_dimension_id IS NOT NULL",
            name="target_present",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("length(btrim(strategy)) > 0", name="strategy_nonempty"),
        CheckConstraint("length(btrim(rationale)) > 0", name="rationale_nonempty"),
        UniqueConstraint("recommendation_key", name="uq_retest_recommendations_key"),
        Index(
            "ix_retest_recommendations_user_status",
            "user_id",
            "status",
            "recommended_after",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    breakpoint_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("breakpoints.id", ondelete="SET NULL")
    )
    concept_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT")
    )
    skill_dimension_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("skill_dimensions.id", ondelete="RESTRICT")
    )
    recommended_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    generation_policy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    recommendation_key: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class RetestAttempt(Base):
    __tablename__ = "retest_attempts"
    __table_args__ = (
        UniqueConstraint(
            "retest_recommendation_id",
            "interview_session_id",
            name="uq_retest_attempts_recommendation_session",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completion_after_start"
        ),
        CheckConstraint("outcome IS NULL OR length(btrim(outcome)) > 0", name="outcome_nonempty"),
        ForeignKeyConstraint(
            ["interview_session_id", "interviewer_prompt_id"],
            ["interviewer_prompts.interview_session_id", "interviewer_prompts.id"],
            name="fk_retest_attempts_session_prompt",
            ondelete="SET NULL (interviewer_prompt_id)",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    retest_recommendation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("retest_recommendations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    interviewer_prompt_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(64))

    recommendation: Mapped[RetestRecommendation] = relationship(
        foreign_keys=[retest_recommendation_id]
    )
    interview_session: Mapped[InterviewSession] = relationship(foreign_keys=[interview_session_id])
    interviewer_prompt: Mapped[InterviewerPrompt | None] = relationship(
        foreign_keys=[interviewer_prompt_id]
    )


class RetestAttemptEvidence(Base):
    __tablename__ = "retest_attempt_evidence"

    retest_attempt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("retest_attempts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )

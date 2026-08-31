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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.constants import (
    DELIVERY_STATES,
    INTERVIEW_LEVELS,
    INTERVIEW_MODES,
    INTERVIEW_SESSION_STATUSES,
    INTERVIEW_STAGES,
    PROBE_STRATEGIES,
    PROMPT_KINDS,
    PROMPT_ORIGINS,
    PROMPT_STATUSES,
    RESPONSE_COMPLETION_REASONS,
    RESPONSE_SOURCE_ROLES,
)
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.ai_gateway.models import AIInvocation
    from app.auth.models import User
    from app.examiner.models import CandidateClaim, ExaminerDecision
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
        ForeignKeyConstraint(
            ["interview_pack_version_id", "problem_version_id"],
            ["interview_pack_versions.id", "interview_pack_versions.problem_version_id"],
            name="fk_interview_sessions_pack_problem_version",
        ),
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
        foreign_keys=[interview_pack_version_id],
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
    stage_transitions: Mapped[list[InterviewStageTransition]] = relationship(
        back_populates="interview_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="InterviewStageTransition.interview_session_id",
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


class InterviewStageTransition(Base):
    __tablename__ = "interview_stage_transitions"
    __table_args__ = (
        CheckConstraint(_in_values("from_stage", INTERVIEW_STAGES), name="from_stage"),
        CheckConstraint(_in_values("to_stage", INTERVIEW_STAGES), name="to_stage"),
        CheckConstraint("state_version > 0", name="state_version_positive"),
        CheckConstraint("from_stage <> to_stage", name="stage_changed"),
        ForeignKeyConstraint(
            ["interview_session_id", "event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_stage_transitions_session_event",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "interview_session_id",
            "id",
            name="uq_stage_transitions_session_id",
        ),
        UniqueConstraint(
            "interview_session_id",
            "state_version",
            name="uq_stage_transitions_session_state_version",
        ),
        UniqueConstraint("event_id", name="uq_stage_transitions_event"),
        Index(
            "ix_stage_transitions_session_state_version",
            "interview_session_id",
            "state_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(96), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    transition_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship(
        back_populates="stage_transitions",
        foreign_keys=[interview_session_id],
    )
    event: Mapped[InterviewEvent] = relationship(foreign_keys=[event_id])


class InterviewerPrompt(Base):
    __tablename__ = "interviewer_prompts"
    __table_args__ = (
        CheckConstraint(_in_values("origin", PROMPT_ORIGINS), name="origin"),
        CheckConstraint(_in_values("kind", PROMPT_KINDS), name="kind"),
        CheckConstraint(
            f"probe_strategy IS NULL OR {_in_values('probe_strategy', PROBE_STRATEGIES)}",
            name="probe_strategy",
        ),
        CheckConstraint(
            "(kind = 'PROBE' AND probe_strategy IS NOT NULL) OR "
            "(kind <> 'PROBE' AND probe_strategy IS NULL)",
            name="probe_strategy_matches_kind",
        ),
        CheckConstraint(
            "(origin = 'EXAMINER_DECISION' AND examiner_decision_id IS NOT NULL) OR "
            "(origin <> 'EXAMINER_DECISION' AND examiner_decision_id IS NULL)",
            name="examiner_decision_matches_origin",
        ),
        CheckConstraint(_in_values("status", PROMPT_STATUSES), name="status"),
        ForeignKeyConstraint(
            ["interview_session_id", "examiner_decision_id"],
            ["examiner_decisions.interview_session_id", "examiner_decisions.id"],
            name="fk_interviewer_prompts_session_examiner_decision",
            ondelete="SET NULL (examiner_decision_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "source_stage_transition_id"],
            [
                "interview_stage_transitions.interview_session_id",
                "interview_stage_transitions.id",
            ],
            name="fk_interviewer_prompts_session_stage_transition",
            ondelete="SET NULL (source_stage_transition_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "target_claim_id"],
            ["candidate_claims.interview_session_id", "candidate_claims.id"],
            name="fk_interviewer_prompts_session_claim",
            ondelete="SET NULL (target_claim_id)",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_interviewer_prompts_session_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    examiner_decision_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("examiner_decisions.id", ondelete="SET NULL"),
    )
    origin: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    probe_strategy: Mapped[str | None] = mapped_column(String(64))
    source_stage_transition_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_claim_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("candidate_claims.id", ondelete="SET NULL"),
    )
    target_concept_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_skill_dimension_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship()
    examiner_decision: Mapped[ExaminerDecision | None] = relationship(
        foreign_keys=[examiner_decision_id],
    )
    source_stage_transition: Mapped[InterviewStageTransition | None] = relationship(
        foreign_keys=[source_stage_transition_id],
    )
    target_claim: Mapped[CandidateClaim | None] = relationship(foreign_keys=[target_claim_id])
    deliveries: Mapped[list[InterviewerPromptDelivery]] = relationship(
        back_populates="interviewer_prompt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="InterviewerPromptDelivery.interviewer_prompt_id",
    )
    responses: Mapped[list[CandidateResponse]] = relationship(
        back_populates="interviewer_prompt",
        foreign_keys="CandidateResponse.interviewer_prompt_id",
    )


class InterviewerPromptDelivery(Base):
    __tablename__ = "interviewer_prompt_deliveries"
    __table_args__ = (
        CheckConstraint("delivery_attempt > 0", name="delivery_attempt_positive"),
        CheckConstraint(_in_values("delivery_state", DELIVERY_STATES), name="delivery_state"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        CheckConstraint(
            "interrupted_at IS NULL OR interrupted_at >= started_at",
            name="interrupted_after_started",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "interviewer_prompt_id"],
            ["interviewer_prompts.interview_session_id", "interviewer_prompts.id"],
            name="fk_prompt_deliveries_session_prompt",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "actual_transcript_segment_id"],
            ["transcript_segments.interview_session_id", "transcript_segments.id"],
            name="fk_prompt_deliveries_session_transcript",
            ondelete="SET NULL (actual_transcript_segment_id)",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_prompt_deliveries_session_ai_invocation",
            ondelete="SET NULL (ai_invocation_id)",
        ),
        UniqueConstraint(
            "interviewer_prompt_id",
            "delivery_attempt",
            name="uq_prompt_deliveries_prompt_attempt",
        ),
        Index(
            "uq_prompt_deliveries_one_started_per_session",
            "interview_session_id",
            unique=True,
            postgresql_where=text("delivery_state = 'STARTED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    interviewer_prompt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interviewer_prompts.id", ondelete="CASCADE"),
        nullable=False,
    )
    delivery_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    intended_text: Mapped[str] = mapped_column(Text, nullable=False)
    actual_transcript_segment_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("transcript_segments.id", ondelete="SET NULL"),
    )
    delivery_state: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realtime_provider_event_id: Mapped[str | None] = mapped_column(String(256))
    ai_invocation_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_invocations.id", ondelete="SET NULL"),
    )

    interviewer_prompt: Mapped[InterviewerPrompt] = relationship(
        back_populates="deliveries",
        foreign_keys=[interviewer_prompt_id],
    )
    actual_transcript_segment: Mapped[TranscriptSegment | None] = relationship(
        foreign_keys=[actual_transcript_segment_id],
    )
    ai_invocation: Mapped[AIInvocation | None] = relationship(foreign_keys=[ai_invocation_id])


class CandidateResponse(Base):
    __tablename__ = "candidate_responses"
    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        CheckConstraint(
            _in_values("completion_reason", RESPONSE_COMPLETION_REASONS),
            name="completion_reason",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "interviewer_prompt_id"],
            ["interviewer_prompts.interview_session_id", "interviewer_prompts.id"],
            name="fk_candidate_responses_session_prompt",
            ondelete="SET NULL (interviewer_prompt_id)",
        ),
        UniqueConstraint("interview_session_id", "id", name="uq_candidate_responses_session_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    interviewer_prompt_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interviewer_prompts.id", ondelete="SET NULL"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    interview_session: Mapped[InterviewSession] = relationship()
    interviewer_prompt: Mapped[InterviewerPrompt | None] = relationship(
        back_populates="responses",
        foreign_keys=[interviewer_prompt_id],
    )
    sources: Mapped[list[CandidateResponseSource]] = relationship(
        back_populates="candidate_response",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="CandidateResponseSource.candidate_response_id",
    )


class CandidateResponseSource(Base):
    __tablename__ = "candidate_response_sources"
    __table_args__ = (
        CheckConstraint(_in_values("source_role", RESPONSE_SOURCE_ROLES), name="source_role"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        ForeignKeyConstraint(
            ["interview_session_id", "candidate_response_id"],
            ["candidate_responses.interview_session_id", "candidate_responses.id"],
            name="fk_candidate_response_sources_session_response",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_candidate_response_sources_session_event",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "candidate_response_id",
            "sequence",
            name="uq_candidate_response_sources_response_sequence",
        ),
    )

    interview_session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    candidate_response_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("candidate_responses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    interview_event_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("interview_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_role: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    candidate_response: Mapped[CandidateResponse] = relationship(
        back_populates="sources",
        foreign_keys=[candidate_response_id],
    )
    interview_event: Mapped[InterviewEvent] = relationship(foreign_keys=[interview_event_id])

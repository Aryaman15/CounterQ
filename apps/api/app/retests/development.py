"""Durable development-only seed for founder retest acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIPolicyVersion
from app.ai_gateway.repository import AIInvocationRepository
from app.auth.models import User
from app.auth.repository import UserRepository
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointService,
)
from app.evidence.contracts import (
    AssessmentSourceInput,
    CreateAssessmentCommand,
    EvidenceConceptInput,
    EvidenceSkillInput,
    EvidenceSourceInput,
    ValidateEvidenceCommand,
)
from app.evidence.models import Evidence, SkillDimension
from app.evidence.validation import EvidenceValidationService
from app.interviews.mode_policy import ModePolicy
from app.interviews.models import InterviewSession
from app.interviews.repository import InterviewRepository
from app.interviews.template_policy import template_policy
from app.mastery.models import RetestRecommendation
from app.problems.content import validate_authored_content
from app.problems.models import Concept, Problem, ProblemVersion
from app.problems.service import CuratedProblemService

DEVELOPMENT_RETEST_SUBJECT = "stage8b-founder-retest-candidate"
DEVELOPMENT_RETEST_POLICY_KEY = "stage8b_development_fixture"


async def ensure_development_retest_fixture(session: AsyncSession) -> User:
    ontology, entries = validate_authored_content()
    curated = CuratedProblemService(session)
    await curated.seed_ontology(ontology)
    for entry in entries:
        await curated.seed_problem(entry)

    user = await session.scalar(
        select(User).where(
            User.external_auth_provider == "dev",
            User.external_auth_subject == DEVELOPMENT_RETEST_SUBJECT,
        )
    )
    if user is None:
        user = await UserRepository(session).add(
            external_auth_provider="dev",
            external_auth_subject=DEVELOPMENT_RETEST_SUBJECT,
        )
    existing = await session.scalar(
        select(InterviewSession)
        .where(InterviewSession.user_id == user.id, InterviewSession.status == "COMPLETED")
        .limit(1)
    )
    if existing is not None:
        return user

    problem = await session.scalar(
        select(Problem).where(
            Problem.source_type == "CURATED",
            Problem.slug == "longest-substring-without-repeating-characters",
        )
    )
    assert problem is not None
    version = await session.scalar(
        select(ProblemVersion)
        .where(ProblemVersion.problem_id == problem.id)
        .order_by(ProblemVersion.version.desc())
        .limit(1)
    )
    assert version is not None
    pack = await curated.reviewed_pack_for_problem(version.id)
    concept = await session.scalar(
        select(Concept).where(Concept.canonical_key == "sliding_window_invariant")
    )
    assert concept is not None
    skill = await session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == "correctness")
    )
    if skill is None:
        skill = SkillDimension(
            canonical_key="correctness",
            display_name="Correctness",
            description="Whether reasoning and implementation satisfy the problem requirements.",
            status="ACTIVE",
        )
        session.add(skill)
        await session.flush()

    now = datetime.now(UTC)
    started = now - timedelta(days=14, minutes=30)
    completed = now - timedelta(days=14)
    interviews = InterviewRepository(session)
    policy = template_policy("STANDARD_CODING_INTERVIEW")
    assert policy.configured_duration_seconds is not None
    configuration = await interviews.add_configuration(
        mode="COACH",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=policy.configured_duration_seconds,
        problem_source="CURATED",
    )
    interview = await interviews.add_session(
        user_id=user.id,
        configuration_id=configuration.id,
        problem_version_id=version.id,
        interview_pack_version_id=pack.id,
        current_stage="COMPLETED",
        state_version=10,
        status="COMPLETED",
        started_at=started,
        deadline_at=started + timedelta(seconds=policy.configured_duration_seconds),
    )
    interview.completed_at = completed
    assistance = ModePolicy().assistance_budget("COACH")
    await interviews.add_budget(
        session_id=interview.id,
        max_duration_seconds=policy.configured_duration_seconds,
        max_probes=policy.max_probes,
        max_deep_reasoning_calls=policy.max_deep_reasoning_calls,
        reserved_post_interview_deep_reasoning_calls=(
            policy.reserved_post_interview_deep_reasoning_calls
        ),
        max_strong_reasoning_calls=policy.max_strong_reasoning_calls,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5000"),
        hard_monetary_budget=Decimal("5.0000"),
        realtime_reserved_budget=Decimal("1.2500"),
        max_assistance_interventions=assistance.max_assistance_interventions,
        max_structural_hints=assistance.max_structural_hints,
        max_direct_teaching_interventions=assistance.max_direct_teaching_interventions,
        max_guided_retries=assistance.max_guided_retries,
    )
    negative_event = await interviews.add_event(
        session_id=interview.id,
        user_id=user.id,
        event_type="TRANSCRIPT_FINALIZED",
        source="CANDIDATE_VOICE",
        occurred_at=started + timedelta(minutes=12),
        received_at=started + timedelta(minutes=12),
        server_sequence=1,
        interview_state_version=6,
        schema_version="transcript.final.v1",
        idempotency_key=f"{DEVELOPMENT_RETEST_POLICY_KEY}:negative",
        payload={"fixture": "independent diagnostic reasoning"},
    )
    taught_event = await interviews.add_event(
        session_id=interview.id,
        user_id=user.id,
        event_type="TRANSCRIPT_FINALIZED",
        source="CANDIDATE_VOICE",
        occurred_at=started + timedelta(minutes=20),
        received_at=started + timedelta(minutes=20),
        server_sequence=2,
        interview_state_version=7,
        schema_version="transcript.final.v1",
        idempotency_key=f"{DEVELOPMENT_RETEST_POLICY_KEY}:taught",
        payload={"fixture": "post-teaching explanation"},
    )
    interview.last_server_sequence = 2
    fixture_policy = await session.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.policy_key == DEVELOPMENT_RETEST_POLICY_KEY,
            AIPolicyVersion.version == "v1",
        )
    )
    if fixture_policy is None:
        fixture_policy = AIPolicyVersion(
            policy_key=DEVELOPMENT_RETEST_POLICY_KEY,
            version="v1",
            prompt_hash=None,
            configuration_json={"kind": "deterministic_development_fixture", "live_ai": False},
            activated_at=now,
        )
        session.add(fixture_policy)
        await session.flush()
    validation = EvidenceValidationService(session)
    validation_policy = await validation.ensure_validation_policy_version()
    negative = await _add_evidence(
        session,
        validation,
        fixture_policy,
        validation_policy,
        interview,
        concept,
        skill,
        negative_event.id,
        occurred_at=negative_event.occurred_at,
        polarity="NEGATIVE",
        strength="STRONG",
        independence="INDEPENDENT",
        finding="Could not defend why the left boundary must move monotonically.",
    )
    await _add_evidence(
        session,
        validation,
        fixture_policy,
        validation_policy,
        interview,
        concept,
        skill,
        taught_event.id,
        occurred_at=taught_event.occurred_at,
        polarity="POSITIVE",
        strength="MODERATE",
        independence="DIRECTLY_TAUGHT",
        finding="Explained the left-boundary invariant after direct teaching.",
    )
    result = await BreakpointService(session).create_or_reinforce(
        BreakpointCandidate(
            user_id=user.id,
            interview_session_id=interview.id,
            concept_id=concept.id,
            skill_dimension_id=skill.id,
            assessment_dimension="CORRECTNESS",
            evidence_ids=(negative.id,),
            boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
            summary="Left-boundary monotonicity still needs independent verification.",
            severity="HIGH",
            known_subtype="left_pointer_monotonicity",
        )
    )
    assert result.breakpoint_id is not None
    return user


async def _add_evidence(
    session: AsyncSession,
    validation: EvidenceValidationService,
    evaluator_policy: AIPolicyVersion,
    validation_policy: AIPolicyVersion,
    interview: InterviewSession,
    concept: Concept,
    skill: SkillDimension,
    event_id: UUID,
    *,
    occurred_at: datetime,
    polarity: str,
    strength: str,
    independence: str,
    finding: str,
) -> Evidence:
    invocation = await AIInvocationRepository(session).add(
        user_id=interview.user_id,
        interview_session_id=interview.id,
        ai_policy_version_id=evaluator_policy.id,
        provider="deterministic-fixture",
        model="none",
        capability="STANDARD_REASONING",
        purpose="candidate_response_assessment",
        status="SUCCEEDED",
        started_at=occurred_at,
        completed_at=occurred_at,
        estimated_cost=Decimal("0"),
    )
    assessment = await validation.create_assessment(
        CreateAssessmentCommand(
            interview_session_id=interview.id,
            assessment_dimension="CORRECTNESS",
            polarity=polarity,
            rationale=finding,
            confidence=Decimal("0.9500"),
            status="VALIDATED",
            ai_invocation_id=invocation.id,
            ai_policy_version_id=evaluator_policy.id,
            sources=(AssessmentSourceInput(event_id, "PRIMARY", 1),),
        )
    )
    accepted = await validation.validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=interview.id,
            assessment_id=assessment.id,
            polarity=polarity,
            strength=strength,
            confidence=Decimal("0.9500"),
            finding=finding,
            independence_level=independence,
            validation_policy_version_id=validation_policy.id,
            sources=(EvidenceSourceInput(event_id, "PRIMARY"),),
            concepts=(EvidenceConceptInput(concept.id, Decimal("1"), True),),
            skills=(EvidenceSkillInput(skill.id, Decimal("1"), True),),
        )
    )
    assert accepted.accepted and accepted.evidence_id is not None
    evidence = await session.get(Evidence, accepted.evidence_id)
    assert evidence is not None
    return evidence


async def active_development_recommendation(
    session: AsyncSession, user_id: UUID
) -> RetestRecommendation | None:
    result = await session.scalar(
        select(RetestRecommendation)
        .where(
            RetestRecommendation.user_id == user_id,
            RetestRecommendation.status.in_(("PENDING", "SCHEDULED")),
            RetestRecommendation.concept_id.is_not(None),
        )
        .order_by(
            RetestRecommendation.priority.desc(),
            RetestRecommendation.recommended_after,
            RetestRecommendation.created_at,
            RetestRecommendation.id,
        )
        .limit(1)
    )
    return result

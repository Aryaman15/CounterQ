from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_stage1_1a_persistence import Stage1PersistenceGraph, add_event, create_stage1_graph
from test_stage1_1b_causal_persistence import (
    add_snapshot,
    add_transcript_segment,
    create_ai_context,
)

from app.db.constants import BREAKPOINT_EVIDENCE_RELATIONSHIPS, SKILL_DIMENSION_KEYS
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointService,
    normalize_breakpoint_key,
)
from app.evidence.contracts import (
    AssessmentSourceInput,
    CreateAssessmentCommand,
    EvidenceConceptInput,
    EvidenceSkillInput,
    EvidenceSourceInput,
    ValidateEvidenceCommand,
)
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.evidence.repository import EvidenceRepository
from app.evidence.validation import AssessmentValidationError, EvidenceValidationService
from app.examiner.repository import ExaminerRepository
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.observation.models import InterviewEvent
from app.problems.models import Concept


@dataclass(frozen=True)
class EvidenceFixture:
    graph: Stage1PersistenceGraph
    event: InterviewEvent
    assessment: Assessment
    concept: Concept
    skill: SkillDimension
    validation_policy_id: UUID


async def canonical_concept(
    db_session: AsyncSession,
    *,
    canonical_key: str = "hash_table_complexity",
) -> Concept:
    existing = await db_session.scalar(
        select(Concept).where(Concept.canonical_key == canonical_key)
    )
    if existing is not None:
        return existing
    concept = Concept(
        canonical_key=canonical_key,
        display_name=canonical_key.replace("_", " ").title(),
        category="DATA_STRUCTURES",
        status="ACTIVE",
        description="Canonical deterministic Stage 5 test concept.",
    )
    db_session.add(concept)
    await db_session.flush()
    return concept


async def skill_by_key(db_session: AsyncSession, canonical_key: str) -> SkillDimension:
    skill = await db_session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == canonical_key)
    )
    assert skill is not None
    return skill


async def evidence_fixture(db_session: AsyncSession) -> EvidenceFixture:
    graph = await create_stage1_graph(db_session)
    event = await add_event(db_session, graph, server_sequence=1)
    ai = await create_ai_context(db_session, graph, purpose="ASSESSMENT")
    service = EvidenceValidationService(db_session)
    validation_policy = await service.ensure_validation_policy_version()
    assessment = await service.create_assessment(
        CreateAssessmentCommand(
            interview_session_id=graph.interview_session.id,
            assessment_dimension="CORRECTNESS",
            polarity="NEGATIVE",
            rationale="Candidate treats average-case behavior as a guarantee.",
            confidence=Decimal("0.9100"),
            status="VALIDATED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
            sources=(AssessmentSourceInput(event.id, "PRIMARY", 1),),
        )
    )
    return EvidenceFixture(
        graph=graph,
        event=event,
        assessment=assessment,
        concept=await canonical_concept(db_session),
        skill=await skill_by_key(db_session, "complexity_reasoning"),
        validation_policy_id=validation_policy.id,
    )


async def validate_evidence(
    db_session: AsyncSession,
    fixture: EvidenceFixture,
    *,
    polarity: str = "NEGATIVE",
    strength: str = "STRONG",
    confidence: Decimal = Decimal("0.9000"),
    finding: str = "The candidate did not account for adversarial hash collisions.",
) -> Evidence:
    result = await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=fixture.graph.interview_session.id,
            assessment_id=fixture.assessment.id,
            polarity=polarity,
            strength=strength,
            confidence=confidence,
            finding=finding,
            independence_level="INDEPENDENT",
            validation_policy_version_id=fixture.validation_policy_id,
            sources=(EvidenceSourceInput(fixture.event.id, "PRIMARY"),),
            concepts=(EvidenceConceptInput(fixture.concept.id, Decimal("1.0000"), True),),
            skills=(EvidenceSkillInput(fixture.skill.id, Decimal("1.0000"), True),),
        )
    )
    assert result.accepted is True
    assert result.evidence_id is not None
    evidence = await db_session.get(Evidence, result.evidence_id)
    assert evidence is not None
    return evidence


async def test_skill_dimensions_are_seeded_with_only_frozen_canonical_keys(
    db_session: AsyncSession,
) -> None:
    keys = tuple(
        await db_session.scalars(select(SkillDimension.canonical_key).order_by(SkillDimension.id))
    )

    assert len(keys) == 10
    assert set(keys) == set(SKILL_DIMENSION_KEYS)


async def test_assessment_accepts_response_claim_snapshot_and_event_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    transcript_event, transcript = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="Hash lookup is always constant time.",
    )
    snapshot_event, snapshot = await add_snapshot(
        db_session, graph, server_sequence=2, version_number=1
    )
    response = await InterviewInteractionRepository(db_session).add_response(
        interview_session_id=graph.interview_session.id,
        started_at=datetime.now(UTC),
        completion_reason="COMPLETE",
    )
    await InterviewInteractionRepository(db_session).add_response_source(
        interview_session_id=graph.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=transcript_event.id,
        source_role="PRIMARY",
        sequence=1,
    )
    ai = await create_ai_context(db_session, graph, purpose="ASSESSMENT")
    claim = await ExaminerRepository(db_session).add_candidate_claim(
        interview_session_id=graph.interview_session.id,
        origin_kind="TRANSCRIPT",
        source_transcript_segment_id=transcript.id,
        source_event_id=transcript_event.id,
        normalized_claim="hash lookup has guaranteed constant complexity",
        claim_type="COMPLEXITY",
        extraction_confidence=Decimal("0.9400"),
        status="ACCEPTED_AS_INTERPRETATION",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    service = EvidenceValidationService(db_session)
    routes: tuple[dict[str, object], ...] = (
        {"candidate_response_id": response.id},
        {"target_claim_id": claim.id},
        {"source_code_snapshot_id": snapshot.id},
        {"sources": (AssessmentSourceInput(snapshot_event.id, "PRIMARY", 1),)},
    )

    assessments = []
    for route in routes:
        assessments.append(
            await service.create_assessment(
                CreateAssessmentCommand(
                    interview_session_id=graph.interview_session.id,
                    assessment_dimension="DEPTH",
                    polarity="MIXED",
                    rationale="A provenance-complete interpretation.",
                    confidence=Decimal("0.8000"),
                    status="PROPOSED",
                    ai_invocation_id=ai.invocation.id,
                    ai_policy_version_id=ai.policy.id,
                    **route,  # type: ignore[arg-type]
                )
            )
        )

    assert len({assessment.id for assessment in assessments}) == 4
    assert assessments[0].candidate_response_id == response.id
    assert assessments[1].target_claim_id == claim.id
    assert assessments[2].source_code_snapshot_id == snapshot.id
    source = await db_session.scalar(
        select(AssessmentSource).where(AssessmentSource.assessment_id == assessments[3].id)
    )
    assert source is not None
    assert source.interview_event_id == snapshot_event.id


async def test_assessment_rejects_missing_and_cross_session_provenance(
    db_session: AsyncSession,
) -> None:
    graph_a = await create_stage1_graph(db_session)
    graph_b = await create_stage1_graph(db_session)
    event_b = await add_event(db_session, graph_b, server_sequence=1)
    ai = await create_ai_context(db_session, graph_a, purpose="ASSESSMENT")
    base = dict(
        interview_session_id=graph_a.interview_session.id,
        assessment_dimension="CORRECTNESS",
        polarity="NEGATIVE",
        rationale="This interpretation must remain source anchored.",
        confidence=Decimal("0.9000"),
        status="VALIDATED",
        ai_invocation_id=ai.invocation.id,
        ai_policy_version_id=ai.policy.id,
    )
    service = EvidenceValidationService(db_session)

    with pytest.raises(AssessmentValidationError, match="requires") as missing:
        await service.create_assessment(CreateAssessmentCommand(**base))  # type: ignore[arg-type]
    assert missing.value.code == "ASSESSMENT_PROVENANCE_REQUIRED"

    with pytest.raises(AssessmentValidationError, match="Assessment session") as cross_session:
        await service.create_assessment(
            CreateAssessmentCommand(
                **base,  # type: ignore[arg-type]
                sources=(AssessmentSourceInput(event_b.id, "PRIMARY", 1),),
            )
        )
    assert cross_session.value.code == "ASSESSMENT_SOURCE_SESSION_MISMATCH"


async def test_assessment_rejects_values_outside_the_frozen_vocabulary(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(db_session, graph, server_sequence=1)
    ai = await create_ai_context(db_session, graph, purpose="ASSESSMENT")
    service = EvidenceValidationService(db_session)
    invalid_values = (
        ("SPEED", "POSITIVE", "VALIDATED", "INVALID_ASSESSMENT_DIMENSION"),
        ("DEPTH", "UNCERTAIN", "VALIDATED", "INVALID_ASSESSMENT_POLARITY"),
        ("TRANSFER", "MIXED", "ACCEPTED", "INVALID_ASSESSMENT_STATUS"),
    )

    for dimension, polarity, status, expected_code in invalid_values:
        with pytest.raises(AssessmentValidationError) as rejected:
            await service.create_assessment(
                CreateAssessmentCommand(
                    interview_session_id=graph.interview_session.id,
                    assessment_dimension=dimension,
                    polarity=polarity,
                    rationale="Unsupported values must fail before persistence.",
                    confidence=Decimal("0.8000"),
                    status=status,
                    ai_invocation_id=ai.invocation.id,
                    ai_policy_version_id=ai.policy.id,
                    sources=(AssessmentSourceInput(event.id, "PRIMARY", 1),),
                )
            )
        assert rejected.value.code == expected_code


async def test_evidence_is_canonical_only_after_validation_and_retains_all_links(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    second_concept = await canonical_concept(db_session, canonical_key="amortized_analysis")
    second_skill = await skill_by_key(db_session, "explanation_clarity")
    result = await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=fixture.graph.interview_session.id,
            assessment_id=fixture.assessment.id,
            polarity="NEGATIVE",
            strength="STRONG",
            confidence=Decimal("0.9200"),
            finding="The complexity explanation omitted the worst case.",
            independence_level="AFTER_PROBE",
            validation_policy_version_id=fixture.validation_policy_id,
            sources=(EvidenceSourceInput(fixture.event.id, "PRIMARY"),),
            concepts=(
                EvidenceConceptInput(fixture.concept.id, Decimal("1.0000"), True),
                EvidenceConceptInput(second_concept.id, Decimal("0.5000")),
            ),
            skills=(
                EvidenceSkillInput(fixture.skill.id, Decimal("1.0000"), True),
                EvidenceSkillInput(second_skill.id, Decimal("0.6000")),
            ),
        )
    )

    assert result.accepted is True
    assert result.evidence_id is not None
    evidence = await db_session.get(Evidence, result.evidence_id)
    assert evidence is not None
    assert evidence.evidence_type == "CORRECTNESS"
    assert evidence.originating_assessment_id == fixture.assessment.id
    assert evidence.validation_policy_version_id == fixture.validation_policy_id
    assert evidence.validation_policy_version_id != fixture.assessment.ai_policy_version_id
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(EvidenceSource)
            .where(EvidenceSource.evidence_id == evidence.id)
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(EvidenceConcept)
            .where(EvidenceConcept.evidence_id == evidence.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(EvidenceSkill)
            .where(EvidenceSkill.evidence_id == evidence.id)
        )
        == 2
    )


async def test_evidence_may_target_only_a_concept_or_only_a_skill(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    service = EvidenceValidationService(db_session)
    common = dict(
        interview_session_id=fixture.graph.interview_session.id,
        assessment_id=fixture.assessment.id,
        polarity="NEGATIVE",
        strength="MODERATE",
        confidence=Decimal("0.8000"),
        finding="A canonical target-specific finding.",
        independence_level="INDEPENDENT",
        validation_policy_version_id=fixture.validation_policy_id,
        sources=(EvidenceSourceInput(fixture.event.id, "PRIMARY"),),
    )

    concept_only = await service.validate_into_evidence(
        ValidateEvidenceCommand(
            **common,  # type: ignore[arg-type]
            concepts=(EvidenceConceptInput(fixture.concept.id, Decimal("1.0000"), True),),
        )
    )
    skill_only = await service.validate_into_evidence(
        ValidateEvidenceCommand(
            **common,  # type: ignore[arg-type]
            skills=(EvidenceSkillInput(fixture.skill.id, Decimal("1.0000"), True),),
        )
    )

    assert concept_only.accepted is True
    assert skill_only.accepted is True


async def test_evidence_sources_support_the_four_frozen_roles(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    extra_events = [
        await add_event(db_session, fixture.graph, server_sequence=sequence)
        for sequence in range(2, 5)
    ]
    events = (fixture.event, *extra_events)
    roles = ("PRIMARY", "SUPPORTING", "CONTRADICTING", "CONTEXT")
    await EvidenceRepository(db_session).add_assessment_sources(
        assessment_id=fixture.assessment.id,
        interview_session_id=fixture.graph.interview_session.id,
        sources=tuple(
            AssessmentSourceInput(event.id, role, sequence)
            for sequence, (event, role) in enumerate(
                zip(extra_events, roles[1:], strict=True), start=2
            )
        ),
    )

    result = await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=fixture.graph.interview_session.id,
            assessment_id=fixture.assessment.id,
            polarity="MIXED",
            strength="MODERATE",
            confidence=Decimal("0.8000"),
            finding="Several factual events contribute with explicit roles.",
            independence_level="AFTER_PROBE",
            validation_policy_version_id=fixture.validation_policy_id,
            sources=tuple(
                EvidenceSourceInput(event.id, role)
                for event, role in zip(events, roles, strict=True)
            ),
            skills=(EvidenceSkillInput(fixture.skill.id, Decimal("1.0000"), True),),
        )
    )

    assert result.evidence_id is not None
    persisted_roles = set(
        await db_session.scalars(
            select(EvidenceSource.source_role).where(
                EvidenceSource.evidence_id == result.evidence_id
            )
        )
    )
    assert persisted_roles == set(roles)


async def test_evidence_validation_status_is_service_owned_and_db_constrained(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture)

    assert "validation_status" not in ValidateEvidenceCommand.__dataclass_fields__
    assert evidence.validation_status == "VALID"
    assert "ck_evidence_validation_status" in {
        constraint.name
        for constraint in Evidence.__table__.constraints  # type: ignore[attr-defined]
    }


async def test_high_confidence_proposed_assessment_cannot_bypass_validation(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    fixture.assessment.status = "PROPOSED"
    fixture.assessment.confidence = Decimal("1.0000")
    await db_session.flush()

    result = await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=fixture.graph.interview_session.id,
            assessment_id=fixture.assessment.id,
            polarity="NEGATIVE",
            strength="STRONG",
            confidence=Decimal("1.0000"),
            finding="Confidence must not bypass the validation boundary.",
            independence_level="INDEPENDENT",
            validation_policy_version_id=fixture.validation_policy_id,
            sources=(EvidenceSourceInput(fixture.event.id, "PRIMARY"),),
            concepts=(EvidenceConceptInput(fixture.concept.id, Decimal("1.0000"), True),),
        )
    )

    assert result.accepted is False
    assert "ASSESSMENT_NOT_VALIDATED" in {failure.code for failure in result.failures}
    evidence_count = await db_session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.interview_session_id == fixture.graph.interview_session.id)
    )
    assert evidence_count == 0


async def test_evidence_rejection_is_structured_for_invalid_semantics_and_sources(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    unrelated_event = await add_event(db_session, fixture.graph, server_sequence=2)
    result = await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=fixture.graph.interview_session.id,
            assessment_id=fixture.assessment.id,
            polarity="UNKNOWN",
            strength="CERTAIN",
            confidence=Decimal("1.1000"),
            finding=" ",
            independence_level="HELPED",
            validation_policy_version_id=fixture.validation_policy_id,
            sources=(EvidenceSourceInput(unrelated_event.id, "PRIMARY"),),
        )
    )

    codes = {failure.code for failure in result.failures}
    assert result.accepted is False
    assert {
        "INVALID_POLARITY",
        "INVALID_STRENGTH",
        "INVALID_CONFIDENCE",
        "INVALID_INDEPENDENCE",
        "FINDING_REQUIRED",
        "CANONICAL_TARGET_REQUIRED",
        "SOURCE_NOT_ASSESSMENT_PROVENANCE",
    }.issubset(codes)


async def test_evidence_rejects_cross_session_and_derived_only_provenance(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    other = await create_stage1_graph(db_session)
    other_event = await add_event(db_session, other, server_sequence=1)
    service = EvidenceValidationService(db_session)
    common = dict(
        interview_session_id=fixture.graph.interview_session.id,
        assessment_id=fixture.assessment.id,
        polarity="NEGATIVE",
        strength="STRONG",
        confidence=Decimal("0.9000"),
        finding="Canonical truth must remain event sourced.",
        independence_level="INDEPENDENT",
        validation_policy_version_id=fixture.validation_policy_id,
        concepts=(EvidenceConceptInput(fixture.concept.id, Decimal("1.0000"), True),),
    )

    no_source = await service.validate_into_evidence(
        ValidateEvidenceCommand(**common, sources=())  # type: ignore[arg-type]
    )
    cross_session = await service.validate_into_evidence(
        ValidateEvidenceCommand(
            **common,  # type: ignore[arg-type]
            sources=(EvidenceSourceInput(other_event.id, "PRIMARY"),),
        )
    )

    assert {failure.code for failure in no_source.failures} == {"FACTUAL_SOURCE_REQUIRED"}
    assert "SOURCE_SESSION_MISMATCH" in {failure.code for failure in cross_session.failures}
    assert set(EvidenceSource.__table__.columns.keys()) == {
        "evidence_id",
        "interview_event_id",
        "interview_session_id",
        "source_role",
    }


async def test_evidence_invalidation_is_explicit_idempotent_and_append_preserving(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture)
    service = EvidenceValidationService(db_session)

    first = await service.invalidate(
        interview_session_id=fixture.graph.interview_session.id,
        evidence_id=evidence.id,
        reason="Later factual reconciliation found the source malformed.",
    )
    second = await service.invalidate(
        interview_session_id=fixture.graph.interview_session.id,
        evidence_id=evidence.id,
        reason="A different retry reason must not rewrite history.",
    )

    assert first.changed is True
    assert second.changed is False
    assert second.reason == first.reason
    persisted = await db_session.get(Evidence, evidence.id)
    assert persisted is not None
    assert persisted.validation_status == "INVALIDATED"
    assert persisted.invalidated_at is not None
    assert (
        await EvidenceRepository(db_session).active_for_session(fixture.graph.interview_session.id)
        == []
    )


async def test_breakpoint_policy_normalizes_identity_and_prevents_active_duplicates(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture)
    reinforcement = await validate_evidence(
        db_session,
        fixture,
        polarity="MIXED",
        finding="A second meaningful event reinforces the same boundary.",
    )
    candidate = BreakpointCandidate(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(evidence.id, reinforcement.id),
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="Candidate assumes hash table operations have a guaranteed constant bound.",
        severity="MATERIAL",
        known_subtype="worst_case_complexity",
    )
    service = BreakpointService(db_session)

    first = await service.create_or_reinforce(candidate)
    second = await service.create_or_reinforce(candidate)

    assert first.created is True
    assert second.created is False
    assert first.breakpoint_id == second.breakpoint_id
    assert first.breakpoint_key == "hash_table_worst_case_complexity"
    breakpoint_count = await db_session.scalar(
        select(func.count())
        .select_from(Breakpoint)
        .where(Breakpoint.first_detected_session_id == fixture.graph.interview_session.id)
    )
    assert breakpoint_count == 1
    links = list(
        await db_session.scalars(
            select(BreakpointEvidence)
            .where(BreakpointEvidence.breakpoint_id == first.breakpoint_id)
            .order_by(BreakpointEvidence.created_at, BreakpointEvidence.evidence_id)
        )
    )
    assert {link.relationship for link in links} == {"CREATED", "REINFORCED"}


async def test_resolved_breakpoint_is_historical_and_allows_a_new_active_recurrence(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture)
    candidate = BreakpointCandidate(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(evidence.id,),
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="A meaningful misconception recurred after prior resolution.",
        severity="MATERIAL",
    )
    service = BreakpointService(db_session)
    first = await service.create_or_reinforce(candidate)
    assert first.breakpoint_id is not None
    historical = await db_session.get(Breakpoint, first.breakpoint_id)
    assert historical is not None
    historical.status = "RESOLVED"
    historical.resolved_at = datetime.now(UTC)
    historical.resolution_reason = "Later independent evidence supported resolution."
    await db_session.flush()

    recurrence = await service.create_or_reinforce(candidate)

    assert recurrence.created is True
    assert recurrence.breakpoint_id != first.breakpoint_id
    breakpoint_count = await db_session.scalar(
        select(func.count())
        .select_from(Breakpoint)
        .where(Breakpoint.first_detected_session_id == fixture.graph.interview_session.id)
    )
    assert breakpoint_count == 2


async def test_breakpoint_requires_meaningful_strong_canonical_evidence(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(
        db_session,
        fixture,
        strength="WEAK",
        confidence=Decimal("0.5000"),
    )
    base = dict(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(evidence.id,),
        summary="This must not become a Breakpoint.",
        severity="LOW",
    )
    service = BreakpointService(db_session)

    weak = await service.create_or_reinforce(
        BreakpointCandidate(
            **base,  # type: ignore[arg-type]
            boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        )
    )
    syntax = await service.create_or_reinforce(
        BreakpointCandidate(**base, boundary_kind="SYNTAX_ERROR")  # type: ignore[arg-type]
    )

    assert weak.eligibility.reason == "INSUFFICIENT_EVIDENCE_STRENGTH_OR_CONFIDENCE"
    assert syntax.eligibility.reason == "TRIVIAL_OR_TRANSIENT_BOUNDARY"
    breakpoint_count = await db_session.scalar(
        select(func.count())
        .select_from(Breakpoint)
        .where(Breakpoint.first_detected_session_id == fixture.graph.interview_session.id)
    )
    assert breakpoint_count == 0


def test_breakpoint_fallback_key_is_deterministic_and_controlled() -> None:
    assert (
        normalize_breakpoint_key(
            concept_key="hash_table_complexity",
            skill_key="complexity_reasoning",
            assessment_dimension="CORRECTNESS",
            known_subtype=None,
        )
        == "hash_table_complexity_complexity_reasoning_correctness"
    )
    with pytest.raises(ValueError, match="canonical snake_case"):
        normalize_breakpoint_key(
            concept_key="Hash Table",
            skill_key="complexity_reasoning",
            assessment_dimension="CORRECTNESS",
            known_subtype=None,
        )
    with pytest.raises(ValueError, match="dimension is not controlled"):
        normalize_breakpoint_key(
            concept_key="hash_table_complexity",
            skill_key="complexity_reasoning",
            assessment_dimension="SPEED",
            known_subtype=None,
        )


async def test_breakpoint_resolution_support_accepts_only_canonical_evidence(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    negative = await validate_evidence(db_session, fixture)
    candidate = BreakpointCandidate(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(negative.id,),
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="A canonical Breakpoint with later resolution support.",
        severity="MATERIAL",
    )
    service = BreakpointService(db_session)
    created = await service.create_or_reinforce(candidate)
    assert created.breakpoint_id is not None
    positive = await validate_evidence(
        db_session,
        fixture,
        polarity="POSITIVE",
        finding="The candidate later handled the adversarial case independently.",
    )

    await service.link_evidence(
        breakpoint_id=created.breakpoint_id,
        evidence_id=positive.id,
        relationship="RESOLUTION_SUPPORT",
    )
    contradicting = await validate_evidence(
        db_session,
        fixture,
        polarity="MIXED",
        finding="Separate canonical Evidence contradicts part of the boundary.",
    )
    await service.link_evidence(
        breakpoint_id=created.breakpoint_id,
        evidence_id=contradicting.id,
        relationship="CONTRADICTED",
    )

    link = await db_session.get(
        BreakpointEvidence,
        {"breakpoint_id": created.breakpoint_id, "evidence_id": positive.id},
    )
    assert link is not None
    assert link.relationship == "RESOLUTION_SUPPORT"
    relationships = set(
        await db_session.scalars(
            select(BreakpointEvidence.relationship).where(
                BreakpointEvidence.breakpoint_id == created.breakpoint_id
            )
        )
    )
    assert relationships == {"CREATED", "CONTRADICTED", "RESOLUTION_SUPPORT"}
    assert set(BREAKPOINT_EVIDENCE_RELATIONSHIPS) == {
        "CREATED",
        "REINFORCED",
        "CONTRADICTED",
        "RESOLUTION_SUPPORT",
    }
    assert not hasattr(BreakpointEvidence, "assessment_id")

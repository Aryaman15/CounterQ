from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_stage1_1a_persistence import Stage1PersistenceGraph, add_event, create_stage1_graph
from test_stage1_1b_causal_persistence import add_snapshot, create_ai_context
from test_stage5a_canonical_evaluation import canonical_concept, skill_by_key

from app.ai_gateway.models import AIPolicyVersion
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointPolicyError,
    BreakpointService,
)
from app.evidence.contracts import (
    AssessmentSourceInput,
    CreateAssessmentCommand,
    EvidenceConceptInput,
    EvidenceSkillInput,
    EvidenceSourceInput,
    EvidenceValidationResult,
    ValidateEvidenceCommand,
)
from app.evidence.models import Assessment, Breakpoint, BreakpointEvidence, Evidence
from app.evidence.validation import (
    EVIDENCE_VALIDATION_POLICY_CONFIGURATION,
    EVIDENCE_VALIDATION_POLICY_KEY,
    EVIDENCE_VALIDATION_POLICY_VERSION,
    EvidenceValidationService,
)
from app.interviews.repository import InterviewRepository
from app.observation.models import CodeSnapshot, InterviewEvent
from app.problems.models import Concept


async def _create_assessment(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    events: tuple[InterviewEvent, ...] = (),
    source_code_snapshot: CodeSnapshot | None = None,
    assessment_dimension: str = "CORRECTNESS",
) -> tuple[Assessment, UUID]:
    ai = await create_ai_context(db_session, graph, purpose="ASSESSMENT")
    service = EvidenceValidationService(db_session)
    validation_policy = await service.ensure_validation_policy_version()
    assessment = await service.create_assessment(
        CreateAssessmentCommand(
            interview_session_id=graph.interview_session.id,
            assessment_dimension=assessment_dimension,
            polarity="NEGATIVE",
            rationale="Deterministic integrity-policy fixture.",
            confidence=Decimal("0.9100"),
            status="VALIDATED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
            source_code_snapshot_id=(
                source_code_snapshot.id if source_code_snapshot is not None else None
            ),
            sources=tuple(
                AssessmentSourceInput(event.id, "PRIMARY", sequence)
                for sequence, event in enumerate(events, start=1)
            ),
        )
    )
    return assessment, validation_policy.id


async def _validate(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    assessment: Assessment,
    validation_policy_id: UUID,
    sources: tuple[EvidenceSourceInput, ...],
    concept: Concept,
    skill_id: UUID,
    polarity: str = "NEGATIVE",
    strength: str = "STRONG",
    confidence: Decimal = Decimal("0.9000"),
) -> EvidenceValidationResult:
    return await EvidenceValidationService(db_session).validate_into_evidence(
        ValidateEvidenceCommand(
            interview_session_id=graph.interview_session.id,
            assessment_id=assessment.id,
            polarity=polarity,
            strength=strength,
            confidence=confidence,
            finding="Candidate behavior supports a deterministic canonical finding.",
            independence_level="INDEPENDENT",
            validation_policy_version_id=validation_policy_id,
            sources=sources,
            concepts=(EvidenceConceptInput(concept.id, Decimal("1.0000"), True),),
            skills=(EvidenceSkillInput(skill_id, Decimal("1.0000"), True),),
        )
    )


async def _canonical_evidence(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    concept: Concept,
    skill_id: UUID,
    server_sequence: int = 1,
    polarity: str = "NEGATIVE",
    strength: str = "STRONG",
    confidence: Decimal = Decimal("0.9000"),
    assessment_dimension: str = "CORRECTNESS",
) -> Evidence:
    event = await add_event(db_session, graph, server_sequence=server_sequence)
    assessment, validation_policy_id = await _create_assessment(
        db_session,
        graph,
        events=(event,),
        assessment_dimension=assessment_dimension,
    )
    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=validation_policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill_id,
        polarity=polarity,
        strength=strength,
        confidence=confidence,
    )
    assert result.accepted is True
    assert result.evidence_id is not None
    evidence = await db_session.get(Evidence, result.evidence_id)
    assert evidence is not None
    return evidence


async def _same_user_session(
    db_session: AsyncSession,
    graph: Stage1PersistenceGraph,
    *,
    started_at: datetime,
) -> Stage1PersistenceGraph:
    interviews = InterviewRepository(db_session)
    configuration = await interviews.add_configuration(
        mode=graph.configuration.mode,
        level=graph.configuration.level,
        language=graph.configuration.language,
        configured_duration_seconds=graph.configuration.configured_duration_seconds,
        problem_source=graph.configuration.problem_source,
    )
    session = await interviews.add_session(
        user_id=graph.user.id,
        configuration_id=configuration.id,
        problem_version_id=graph.problem_version.id,
        interview_pack_version_id=graph.pack_version.id,
        current_stage="SETUP",
        state_version=0,
        status="ACTIVE",
        started_at=started_at,
        deadline_at=started_at + timedelta(minutes=30),
    )
    return replace(graph, configuration=configuration, interview_session=session)


def _candidate(
    graph: Stage1PersistenceGraph,
    *,
    concept_id: UUID,
    skill_id: UUID,
    evidence_ids: tuple[UUID, ...],
    assessment_dimension: str = "CORRECTNESS",
) -> BreakpointCandidate:
    return BreakpointCandidate(
        user_id=graph.user.id,
        interview_session_id=graph.interview_session.id,
        concept_id=concept_id,
        skill_dimension_id=skill_id,
        assessment_dimension=assessment_dimension,
        evidence_ids=evidence_ids,
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="Candidate crosses a meaningful technical boundary.",
        severity="MATERIAL",
        known_subtype="worst_case_complexity",
    )


async def test_candidate_transcript_supports_evidence_without_candidate_response(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(db_session, graph, server_sequence=1)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "communication")
    assessment, policy_id = await _create_assessment(db_session, graph, events=(event,))

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is True
    assert assessment.candidate_response_id is None


async def test_code_snapshot_supports_direct_evidence_without_candidate_response(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event, snapshot = await add_snapshot(db_session, graph, server_sequence=1, version_number=1)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "correctness")
    assessment, policy_id = await _create_assessment(
        db_session,
        graph,
        source_code_snapshot=snapshot,
    )

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is True
    assert assessment.candidate_response_id is None


async def test_meaningful_code_self_correction_supports_evidence_without_candidate_response(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="MEANINGFUL_CODE_CHANGE",
        source="NATIVE_EDITOR",
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "debugging")
    assessment, policy_id = await _create_assessment(db_session, graph, events=(event,))

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is True
    assert assessment.candidate_response_id is None


@pytest.mark.parametrize("event_type", ["RUN_CLICKED", "COMPILE_COMPLETED", "TEST_COMPLETED"])
async def test_execution_and_debug_events_support_direct_evidence_without_candidate_response(
    db_session: AsyncSession,
    event_type: str,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type=event_type,
        source="NATIVE_RUNNER",
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "debugging")
    assessment, policy_id = await _create_assessment(db_session, graph, events=(event,))

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is True
    assert assessment.candidate_response_id is None


async def test_counterq_delivery_is_context_only_and_cannot_stand_alone(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type="COUNTERQ_UTTERANCE_DELIVERED",
        source="COUNTERQ_VOICE",
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "correctness")
    assessment, policy_id = await _create_assessment(db_session, graph, events=(event,))

    wrong_role = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )
    context_only = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "CONTEXT"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert wrong_role.accepted is False
    assert {failure.code for failure in wrong_role.failures} == {
        "CONTEXT_SOURCE_ROLE_REQUIRED",
        "CANDIDATE_DEMONSTRATION_SOURCE_REQUIRED",
    }
    assert context_only.accepted is False
    assert {failure.code for failure in context_only.failures} == {
        "CANDIDATE_DEMONSTRATION_SOURCE_REQUIRED"
    }


@pytest.mark.parametrize(
    ("event_type", "source"),
    [
        ("STAGE_CHANGED", "INTERVIEW_ORCHESTRATOR"),
        ("REALTIME_DISCONNECTED", "SYSTEM"),
        ("REALTIME_RECONNECTED", "SYSTEM"),
    ],
)
async def test_operational_events_cannot_support_candidate_evidence(
    db_session: AsyncSession,
    event_type: str,
    source: str,
) -> None:
    graph = await create_stage1_graph(db_session)
    event = await add_event(
        db_session,
        graph,
        server_sequence=1,
        event_type=event_type,
        source=source,
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "correctness")
    assessment, policy_id = await _create_assessment(db_session, graph, events=(event,))

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(EvidenceSourceInput(event.id, "PRIMARY"),),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is False
    assert {failure.code for failure in result.failures} == {
        "NON_EVIDENTIARY_SOURCE",
        "CANDIDATE_DEMONSTRATION_SOURCE_REQUIRED",
    }


async def test_counterq_context_is_admitted_only_alongside_candidate_demonstration(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    candidate_event = await add_event(db_session, graph, server_sequence=1)
    counterq_event = await add_event(
        db_session,
        graph,
        server_sequence=2,
        event_type="COUNTERQ_UTTERANCE_DELIVERED",
        source="COUNTERQ_VOICE",
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "correctness")
    assessment, policy_id = await _create_assessment(
        db_session,
        graph,
        events=(candidate_event, counterq_event),
    )

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(
            EvidenceSourceInput(candidate_event.id, "PRIMARY"),
            EvidenceSourceInput(counterq_event.id, "CONTEXT"),
        ),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is True


async def test_every_evidence_source_must_belong_to_assessment_factual_provenance(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    assessed_event = await add_event(db_session, graph, server_sequence=1)
    extra_event = await add_event(db_session, graph, server_sequence=2)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "correctness")
    assessment, policy_id = await _create_assessment(
        db_session,
        graph,
        events=(assessed_event,),
    )

    result = await _validate(
        db_session,
        graph,
        assessment=assessment,
        validation_policy_id=policy_id,
        sources=(
            EvidenceSourceInput(assessed_event.id, "PRIMARY"),
            EvidenceSourceInput(extra_event.id, "SUPPORTING"),
        ),
        concept=concept,
        skill_id=skill.id,
    )

    assert result.accepted is False
    assert {failure.code for failure in result.failures} == {"SOURCE_NOT_ASSESSMENT_PROVENANCE"}


async def test_evidence_validation_v2_preserves_existing_v1_policy_history(
    db_session: AsyncSession,
) -> None:
    v1_configuration: dict[str, object] = {
        "kind": "deterministic_software",
        "requires_validated_assessment": True,
        "requires_factual_event_source": True,
        "requires_canonical_target": True,
    }
    v1 = AIPolicyVersion(
        policy_key=EVIDENCE_VALIDATION_POLICY_KEY,
        version="v1",
        prompt_hash=None,
        configuration_json=v1_configuration,
    )
    db_session.add(v1)
    await db_session.flush()

    v2 = await EvidenceValidationService(db_session).ensure_validation_policy_version()
    persisted_v1 = await db_session.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.policy_key == EVIDENCE_VALIDATION_POLICY_KEY,
            AIPolicyVersion.version == "v1",
        )
    )

    assert EVIDENCE_VALIDATION_POLICY_VERSION == "v2"
    assert v2.version == "v2"
    assert v2.configuration_json == EVIDENCE_VALIDATION_POLICY_CONFIGURATION
    assert persisted_v1 is not None
    assert persisted_v1.id == v1.id
    assert persisted_v1.configuration_json == v1_configuration


async def test_breakpoint_first_detection_comes_from_earliest_qualifying_evidence(
    db_session: AsyncSession,
) -> None:
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    first_graph = await create_stage1_graph(db_session, now=base_time)
    second_graph = await _same_user_session(
        db_session, first_graph, started_at=base_time + timedelta(days=1)
    )
    caller_graph = await _same_user_session(
        db_session, first_graph, started_at=base_time + timedelta(days=2)
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "complexity_reasoning")
    first = await _canonical_evidence(
        db_session,
        first_graph,
        concept=concept,
        skill_id=skill.id,
    )
    second = await _canonical_evidence(
        db_session,
        second_graph,
        concept=concept,
        skill_id=skill.id,
    )
    first.created_at = base_time + timedelta(hours=1)
    second.created_at = base_time + timedelta(days=1, hours=1)
    await db_session.flush()

    result = await BreakpointService(db_session).create_or_reinforce(
        _candidate(
            caller_graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(second.id, first.id),
        )
    )

    assert result.created is True
    assert result.breakpoint_id is not None
    breakpoint = await db_session.get(Breakpoint, result.breakpoint_id)
    assert breakpoint is not None
    assert breakpoint.first_detected_session_id == first_graph.interview_session.id
    assert breakpoint.first_detected_at == first.created_at
    first_link = await db_session.get(
        BreakpointEvidence,
        {"breakpoint_id": result.breakpoint_id, "evidence_id": first.id},
    )
    second_link = await db_session.get(
        BreakpointEvidence,
        {"breakpoint_id": result.breakpoint_id, "evidence_id": second.id},
    )
    assert first_link is not None and first_link.relationship == "CREATED"
    assert second_link is not None and second_link.relationship == "REINFORCED"


async def test_breakpoint_reinforcement_accepts_matching_cross_session_evidence_once(
    db_session: AsyncSession,
) -> None:
    base_time = datetime(2026, 2, 1, tzinfo=UTC)
    first_graph = await create_stage1_graph(db_session, now=base_time)
    second_graph = await _same_user_session(
        db_session, first_graph, started_at=base_time + timedelta(days=1)
    )
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "complexity_reasoning")
    first = await _canonical_evidence(db_session, first_graph, concept=concept, skill_id=skill.id)
    second = await _canonical_evidence(db_session, second_graph, concept=concept, skill_id=skill.id)
    service = BreakpointService(db_session)
    created = await service.create_or_reinforce(
        _candidate(
            first_graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(first.id,),
        )
    )
    assert created.breakpoint_id is not None
    breakpoint = await db_session.get(Breakpoint, created.breakpoint_id)
    assert breakpoint is not None
    original_session_id = breakpoint.first_detected_session_id
    original_detected_at = breakpoint.first_detected_at

    reinforced = await service.create_or_reinforce(
        _candidate(
            second_graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(second.id,),
        )
    )
    retried = await service.create_or_reinforce(
        _candidate(
            second_graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(second.id,),
        )
    )

    assert reinforced.created is False
    assert retried.created is False
    assert reinforced.breakpoint_id == created.breakpoint_id == retried.breakpoint_id
    link = await db_session.get(
        BreakpointEvidence,
        {"breakpoint_id": created.breakpoint_id, "evidence_id": second.id},
    )
    assert link is not None and link.relationship == "REINFORCED"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(BreakpointEvidence)
            .where(
                BreakpointEvidence.breakpoint_id == created.breakpoint_id,
                BreakpointEvidence.evidence_id == second.id,
            )
        )
        == 1
    )
    assert breakpoint.first_detected_session_id == original_session_id
    assert breakpoint.first_detected_at == original_detected_at


@pytest.mark.parametrize("mismatch", ["concept", "skill"])
async def test_unrelated_target_evidence_cannot_reinforce_breakpoint(
    db_session: AsyncSession,
    mismatch: str,
) -> None:
    graph = await create_stage1_graph(db_session)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "complexity_reasoning")
    initial = await _canonical_evidence(
        db_session, graph, concept=concept, skill_id=skill.id, server_sequence=1
    )
    service = BreakpointService(db_session)
    created = await service.create_or_reinforce(
        _candidate(
            graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(initial.id,),
        )
    )
    assert created.breakpoint_id is not None
    other_concept = (
        await canonical_concept(db_session, canonical_key="unrelated_complexity")
        if mismatch == "concept"
        else concept
    )
    other_skill = (
        await skill_by_key(db_session, "edge_case_reasoning") if mismatch == "skill" else skill
    )
    unrelated = await _canonical_evidence(
        db_session,
        graph,
        concept=other_concept,
        skill_id=other_skill.id,
        server_sequence=2,
    )

    result = await service.create_or_reinforce(
        _candidate(
            graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(initial.id, unrelated.id),
        )
    )

    assert result.created is False
    assert result.breakpoint_id is None
    assert result.eligibility.reason == "EVIDENCE_TARGET_MISMATCH"
    assert (
        await db_session.get(
            BreakpointEvidence,
            {"breakpoint_id": created.breakpoint_id, "evidence_id": unrelated.id},
        )
        is None
    )


@pytest.mark.parametrize("mismatch", ["concept", "skill"])
@pytest.mark.parametrize(
    ("relationship", "polarity"),
    [("CONTRADICTED", "POSITIVE"), ("RESOLUTION_SUPPORT", "POSITIVE")],
)
async def test_rebuttal_and_resolution_links_reject_unrelated_targets(
    db_session: AsyncSession,
    mismatch: str,
    relationship: str,
    polarity: str,
) -> None:
    graph = await create_stage1_graph(db_session)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "complexity_reasoning")
    initial = await _canonical_evidence(
        db_session, graph, concept=concept, skill_id=skill.id, server_sequence=1
    )
    service = BreakpointService(db_session)
    created = await service.create_or_reinforce(
        _candidate(
            graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(initial.id,),
        )
    )
    assert created.breakpoint_id is not None
    other_concept = (
        await canonical_concept(db_session, canonical_key="unrelated_rebuttal")
        if mismatch == "concept"
        else concept
    )
    other_skill = (
        await skill_by_key(db_session, "edge_case_reasoning") if mismatch == "skill" else skill
    )
    unrelated = await _canonical_evidence(
        db_session,
        graph,
        concept=other_concept,
        skill_id=other_skill.id,
        server_sequence=2,
        polarity=polarity,
    )

    with pytest.raises(BreakpointPolicyError, match="does not target"):
        await service.link_evidence(
            breakpoint_id=created.breakpoint_id,
            evidence_id=unrelated.id,
            relationship=relationship,
        )

    assert (
        await db_session.get(
            BreakpointEvidence,
            {"breakpoint_id": created.breakpoint_id, "evidence_id": unrelated.id},
        )
        is None
    )


@pytest.mark.parametrize(
    ("relationship", "polarity"),
    [("CONTRADICTED", "MIXED"), ("RESOLUTION_SUPPORT", "POSITIVE")],
)
async def test_rebuttal_and_resolution_links_accept_matching_positive_or_mixed_evidence(
    db_session: AsyncSession,
    relationship: str,
    polarity: str,
) -> None:
    graph = await create_stage1_graph(db_session)
    concept = await canonical_concept(db_session)
    skill = await skill_by_key(db_session, "complexity_reasoning")
    initial = await _canonical_evidence(
        db_session, graph, concept=concept, skill_id=skill.id, server_sequence=1
    )
    service = BreakpointService(db_session)
    created = await service.create_or_reinforce(
        _candidate(
            graph,
            concept_id=concept.id,
            skill_id=skill.id,
            evidence_ids=(initial.id,),
        )
    )
    assert created.breakpoint_id is not None
    later = await _canonical_evidence(
        db_session,
        graph,
        concept=concept,
        skill_id=skill.id,
        server_sequence=2,
        polarity=polarity,
    )

    await service.link_evidence(
        breakpoint_id=created.breakpoint_id,
        evidence_id=later.id,
        relationship=relationship,
    )

    link = await db_session.get(
        BreakpointEvidence,
        {"breakpoint_id": created.breakpoint_id, "evidence_id": later.id},
    )
    assert link is not None and link.relationship == relationship

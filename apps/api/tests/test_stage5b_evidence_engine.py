from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_ai_gateway import FakeReasoningProvider
from test_stage5a_canonical_evaluation import evidence_fixture, validate_evidence

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import ReasoningProviderError
from app.auth.models import User
from app.config.settings import create_settings, get_settings
from app.db.session import build_engine
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointService,
)
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.evidence.independence import IndependenceAttributionService
from app.evidence.models import (
    Assessment,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceSource,
)
from app.evidence.snapshot import canonical_evaluation_snapshot
from app.evidence.units import AssessmentInputBuilder
from app.evidence.validation import EvidenceValidationService
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import CandidateResponse, InterviewerPrompt, InterviewSession
from app.main import create_app
from app.observation.models import InterviewEvent
from app.problems.models import Concept, ProblemConcept
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
)
from app.realtime.control_service import RealtimeControlService


def _client(sequence: int) -> dict[str, object]:
    return {
        "client_event_id": f"stage5-client-{sequence}",
        "client_instance_id": "stage5-client",
        "client_sequence": sequence,
    }


def _analysis_output(
    *,
    concept_key: str,
    dimension: str = "CORRECTNESS",
    polarity: str = "NEGATIVE",
    source_aliases: list[str] | None = None,
    skill_keys: list[str] | None = None,
    weakness: bool = False,
) -> dict[str, Any]:
    return {
        "findings": [
            {
                "assessment_dimension": dimension,
                "polarity": polarity,
                "confidence": 0.93,
                "technical_rationale": (
                    "The bounded factual response supports this technical interpretation."
                ),
                "evidence_finding": (
                    "Candidate behavior demonstrates the assessed exact technical boundary."
                ),
                "proposed_strength": "STRONG",
                "source_aliases": source_aliases or ["source_1"],
                "concept_keys": [concept_key],
                "skill_dimension_keys": skill_keys or ["correctness"],
                "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY" if weakness else "NONE",
                "breakpoint_subtype": None,
                "breakpoint_effect": "WEAKNESS" if weakness else "NONE",
                "breakpoint_severity": "HIGH" if weakness else None,
            }
        ]
    }


def test_stage5_development_routes_are_blocked_in_production(tmp_path: Path) -> None:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "production"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        post = client.post(
            "/api/evidence/development/session-evaluation",
            json={"interview_session_id": "00000000-0000-0000-0000-000000000001"},
        )
        get = client.get(
            "/api/evidence/development/session-evaluation/00000000-0000-0000-0000-000000000001"
        )

    assert post.status_code == 403
    assert get.status_code == 403


async def _prompt(
    session: AsyncSession,
    interview_id: UUID,
    *,
    kind: str,
    text: str,
) -> InterviewerPrompt:
    return await InterviewInteractionRepository(session).add_prompt(
        interview_session_id=interview_id,
        origin="SYSTEM",
        kind=kind,
        probe_strategy="WHY" if kind == "PROBE" else None,
        intent=text,
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )


async def _deliver(
    service: RealtimeControlService,
    interview_id: UUID,
    prompt: InterviewerPrompt,
    *,
    sequence: int,
    actual: str,
) -> None:
    start = await service.start_delivery(
        session_id=interview_id,
        message=CounterQDeliveryStartedMessage(
            **_client(sequence),
            type="counterq_delivery_started",
            interviewer_prompt_id=prompt.id,
            intended_text=prompt.intent,
            provider_response_id=f"response-{sequence}",
        ),
    )
    await service.complete_delivery(
        session_id=interview_id,
        message=CounterQDeliveryCompletedMessage(
            **_client(sequence + 1),
            type="counterq_delivery_completed",
            interviewer_prompt_id=prompt.id,
            prompt_delivery_id=start.delivery_id,
            provider_response_id=f"response-{sequence}",
            transcript=actual,
        ),
    )


async def _candidate_turn(
    service: RealtimeControlService,
    interview_id: UUID,
    *,
    sequence: int,
    provider_item_id: str,
    transcript: str,
) -> UUID:
    result = await service.persist_candidate_transcript(
        session_id=interview_id,
        message=CandidateTranscriptFinalizedMessage(
            **_client(sequence),
            type="candidate_transcript_finalized",
            provider_item_id=provider_item_id,
            transcript=transcript,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC) + timedelta(seconds=1),
        ),
    )
    return result.event_id


async def _attach_problem_concept(
    session: AsyncSession, interview: InterviewSession, *, key: str
) -> Concept:
    concept = Concept(
        canonical_key=key,
        display_name=key.replace("_", " ").title(),
        category="ALGORITHMS",
        status="ACTIVE",
        description="Stage 5B exact-session canonical concept.",
    )
    session.add(concept)
    await session.flush()
    session.add(
        ProblemConcept(
            problem_version_id=interview.problem_version_id,
            concept_id=concept.id,
            relevance="CORE",
            expected_importance="HIGH",
            role="PRIMARY",
        )
    )
    await session.flush()
    return concept


async def _create_committed_response_session(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, str, UUID]:
    async with sessions() as session, session.begin():
        dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        dev.budget.max_deep_reasoning_calls = 20
        concept_key = f"stage5_admission_{str(dev.interview_session.id).replace('-', '_')}"
        concept = await _attach_problem_concept(session, dev.interview_session, key=concept_key)
        await _candidate_turn(
            RealtimeControlService(session),
            dev.interview_session.id,
            sequence=1,
            provider_item_id=f"admission-{dev.interview_session.id}",
            transcript="This is one bounded finalized candidate demonstration.",
        )
        await InterviewCompletionService(session).complete(
            session_id=dev.interview_session.id,
            reason="USER_ENDED",
            expected_state_version=dev.interview_session.state_version,
            idempotency_key=f"stage5-admission-complete:{dev.interview_session.id}",
        )
        return dev.interview_session.id, dev.user.id, concept_key, concept.id


async def _cleanup_committed_stage5_rows(
    sessions: async_sessionmaker[AsyncSession],
    *,
    user_ids: set[UUID],
    concept_ids: set[UUID],
) -> None:
    async with sessions() as session, session.begin():
        if user_ids:
            await session.execute(delete(Breakpoint).where(Breakpoint.user_id.in_(user_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
        if concept_ids:
            await session.execute(
                delete(ProblemConcept).where(ProblemConcept.concept_id.in_(concept_ids))
            )
            await session.execute(delete(Concept).where(Concept.id.in_(concept_ids)))


async def test_candidate_response_materialization_and_delivery_truth(
    db_session: AsyncSession,
) -> None:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    interview = dev.interview_session
    await _attach_problem_concept(db_session, interview, key="stage5_delivery_truth")
    service = RealtimeControlService(db_session)

    probe = await _prompt(db_session, interview.id, kind="PROBE", text="Undisclosed intent A")
    await _deliver(
        service,
        interview.id,
        probe,
        sequence=1,
        actual="Why does the left pointer stay monotonic?",
    )
    first_event = await _candidate_turn(
        service,
        interview.id,
        sequence=3,
        provider_item_id="candidate-first",
        transcript="Because advancing it never makes an old prefix useful again.",
    )
    # Provider replay converges on the same response source.
    await _candidate_turn(
        service,
        interview.id,
        sequence=4,
        provider_item_id="candidate-first",
        transcript="Because advancing it never makes an old prefix useful again.",
    )
    first_response = await db_session.scalar(
        select(CandidateResponse)
        .join(CandidateResponse.sources)
        .where(CandidateResponse.sources.any(interview_event_id=first_event))
    )
    assert first_response is not None
    assert first_response.interviewer_prompt_id == probe.id
    assert (
        await IndependenceAttributionService(db_session).for_response(first_response)
    ).level == "AFTER_PROBE"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(CandidateResponse)
            .where(CandidateResponse.interview_session_id == interview.id)
        )
        == 1
    )

    authorized_only = await _prompt(
        db_session, interview.id, kind="PROBE", text="Never delivered question"
    )
    await _candidate_turn(
        service,
        interview.id,
        sequence=5,
        provider_item_id="candidate-spontaneous",
        transcript="I will also check the empty string.",
    )
    spontaneous = await db_session.scalar(
        select(CandidateResponse)
        .where(CandidateResponse.interview_session_id == interview.id)
        .order_by(CandidateResponse.created_at.desc(), CandidateResponse.id.desc())
        .limit(1)
    )
    assert spontaneous is not None
    assert spontaneous.interviewer_prompt_id is None
    assert spontaneous.completion_reason == "SPONTANEOUS"
    assert (
        await IndependenceAttributionService(db_session).for_response(spontaneous)
    ).level == "INDEPENDENT"
    assert authorized_only.status == "AUTHORIZED"

    base = await _prompt(db_session, interview.id, kind="BASE_QUESTION", text="Explain it")
    await _deliver(
        service,
        interview.id,
        base,
        sequence=6,
        actual="Walk through the invariant.",
    )
    await _candidate_turn(
        service,
        interview.id,
        sequence=8,
        provider_item_id="candidate-base",
        transcript="The map stores the last seen index.",
    )
    base_response = await db_session.scalar(
        select(CandidateResponse).where(CandidateResponse.interviewer_prompt_id == base.id).limit(1)
    )
    assert base_response is not None
    assert (
        await IndependenceAttributionService(db_session).for_response(base_response)
    ).level == "INDEPENDENT"

    interrupted = await _prompt(
        db_session,
        interview.id,
        kind="PROBE",
        text="SECRET INTENDED REMAINDER MUST NEVER ENTER ASSESSMENT",
    )
    started = await service.start_delivery(
        session_id=interview.id,
        message=CounterQDeliveryStartedMessage(
            **_client(9),
            type="counterq_delivery_started",
            interviewer_prompt_id=interrupted.id,
            intended_text=interrupted.intent,
            provider_response_id="interrupted-response",
        ),
    )
    await service.interrupt_delivery(
        session_id=interview.id,
        message=CounterQDeliveryInterruptedMessage(
            **_client(10),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=interrupted.id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="interrupted-response",
            confirmed_by="output_audio_buffer.cleared",
        ),
    )
    await _candidate_turn(
        service,
        interview.id,
        sequence=11,
        provider_item_id="candidate-interruption",
        transcript="Let me correct that first.",
    )
    interrupted_response = await db_session.scalar(
        select(CandidateResponse)
        .where(CandidateResponse.interview_session_id == interview.id)
        .order_by(CandidateResponse.created_at.desc(), CandidateResponse.id.desc())
        .limit(1)
    )
    assert interrupted_response is not None
    interrupted_attribution = await IndependenceAttributionService(db_session).for_response(
        interrupted_response
    )
    assert interrupted_attribution.level is None
    assert interrupted_attribution.reason == ("INTERRUPTED_PROBE_WITHOUT_EXACT_DELIVERY_TRANSCRIPT")
    await InterviewCompletionService(db_session).complete(
        session_id=interview.id,
        reason="USER_ENDED",
        expected_state_version=interview.state_version,
        idempotency_key="stage5-delivery-complete",
    )
    units = await AssessmentInputBuilder(db_session).build_completed_simulation(interview.id)
    serialized = "\n".join(unit.serialize() for unit in units)
    assert "SECRET INTENDED REMAINDER" not in serialized
    assert "Why does the left pointer stay monotonic?" in serialized
    assert any(unit.independence_level is None for unit in units)


async def test_ambiguous_direct_event_after_probe_has_no_independence_guess(
    db_session: AsyncSession,
) -> None:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = RealtimeControlService(db_session)
    probe = await _prompt(db_session, dev.interview_session.id, kind="PROBE", text="Probe")
    await _deliver(
        service,
        dev.interview_session.id,
        probe,
        sequence=1,
        actual="What happens at the repeated character?",
    )
    result = await service.persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **_client(3),
            type="candidate_code_snapshot",
            source_code="int answer = 0;",
            language="cpp",
            trigger="EDIT_BURST",
        ),
    )
    event = await db_session.get(InterviewEvent, result.event_id)
    assert event is not None

    attribution = await IndependenceAttributionService(db_session).for_direct_event(event)

    assert attribution.level is None
    assert attribution.reason == "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS"


async def test_invalidation_dismisses_breakpoint_when_only_support_disappears(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture)
    result = await BreakpointService(db_session).create_or_reinforce(
        BreakpointCandidate(
            user_id=fixture.graph.user.id,
            interview_session_id=fixture.graph.interview_session.id,
            concept_id=fixture.concept.id,
            skill_dimension_id=fixture.skill.id,
            assessment_dimension="CORRECTNESS",
            evidence_ids=(evidence.id,),
            boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
            summary="A meaningful boundary with one canonical support row.",
            severity="HIGH",
            known_subtype="worst_case_complexity",
        )
    )
    assert result.breakpoint_id is not None

    await EvidenceValidationService(db_session).invalidate(
        interview_session_id=fixture.graph.interview_session.id,
        evidence_id=evidence.id,
        reason="Source was deterministically invalidated.",
    )
    breakpoint = await db_session.get(Breakpoint, result.breakpoint_id)
    links = list(
        await db_session.scalars(
            select(BreakpointEvidence).where(
                BreakpointEvidence.breakpoint_id == result.breakpoint_id
            )
        )
    )
    assert breakpoint is not None
    assert breakpoint.status == "DISMISSED"
    assert breakpoint.resolution_reason == "SUPPORT_INVALIDATED"
    assert len(links) == 1
    assert links[0].relationship == "CREATED"


async def test_invalidation_keeps_breakpoint_with_other_active_support(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    first = await validate_evidence(db_session, fixture)
    second = await validate_evidence(
        db_session,
        fixture,
        finding="A second valid demonstration independently reinforces the boundary.",
    )
    service = BreakpointService(db_session)
    candidate = BreakpointCandidate(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(first.id,),
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="A target with two valid support rows.",
        severity="HIGH",
        known_subtype="worst_case_complexity",
    )
    created = await service.create_or_reinforce(candidate)
    assert created.breakpoint_id is not None
    reinforced = await service.create_or_reinforce(replace(candidate, evidence_ids=(second.id,)))
    assert reinforced.breakpoint_id == created.breakpoint_id

    validation = EvidenceValidationService(db_session)
    await validation.invalidate(
        interview_session_id=fixture.graph.interview_session.id,
        evidence_id=first.id,
        reason="First support invalidated.",
    )
    breakpoint = await db_session.get(Breakpoint, created.breakpoint_id)
    assert breakpoint is not None
    assert breakpoint.status == "OPEN"
    assert await service.active_support_count(breakpoint.id) == 1

    await validation.invalidate(
        interview_session_id=fixture.graph.interview_session.id,
        evidence_id=second.id,
        reason="Second support invalidated.",
    )
    assert breakpoint.status == "DISMISSED"
    assert breakpoint.resolution_reason == "SUPPORT_INVALIDATED"


async def test_assessment_admission_rejects_fabricated_source_concept_and_skill(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        session_id, user_id, concept_key, concept_id = await _create_committed_response_session(
            sessions
        )
        cleanup_user_ids.add(user_id)
        cleanup_concept_ids.add(concept_id)
        source_finding = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key=concept_key,
                dimension="CORRECTNESS",
                source_aliases=["fabricated_source"],
            )["findings"],
        )[0]
        concept_finding = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key="fabricated_concept",
                dimension="DEPTH",
            )["findings"],
        )[0]
        skill_finding = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key=concept_key,
                dimension="INDEPENDENCE",
                skill_keys=["fabricated_skill"],
            )["findings"],
        )[0]
        provider = FakeReasoningProvider(
            output_data={"findings": [source_finding, concept_finding, skill_finding]}
        )
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        ).evaluate(session_id)

        assert result.completed_units == 1
        async with sessions() as session:
            statuses = tuple(
                await session.scalars(
                    select(Assessment.status).where(Assessment.interview_session_id == session_id)
                )
            )
            evidence_count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id == session_id)
            )
            breakpoint_count = await session.scalar(
                select(func.count()).select_from(Breakpoint).where(Breakpoint.user_id == user_id)
            )
        assert statuses == ("REJECTED", "REJECTED", "REJECTED")
        assert evidence_count == 0
        assert breakpoint_count == 0
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_malformed_output_and_provider_failure_create_no_canonical_evidence(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        (
            malformed_session_id,
            malformed_user_id,
            _,
            malformed_concept_id,
        ) = await _create_committed_response_session(sessions)
        (
            failed_session_id,
            failed_user_id,
            concept_key,
            failed_concept_id,
        ) = await _create_committed_response_session(sessions)
        cleanup_user_ids.update((malformed_user_id, failed_user_id))
        cleanup_concept_ids.update((malformed_concept_id, failed_concept_id))
        provider = FakeReasoningProvider(output_data={"findings": [{"malformed": True}]})
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        )

        malformed = await coordinator.evaluate(malformed_session_id)
        provider.output_data = _analysis_output(concept_key=concept_key)
        provider.error = ReasoningProviderError("PROVIDER_UNAVAILABLE")
        failed = await coordinator.evaluate(failed_session_id)

        assert malformed.failed_units == 1
        assert malformed.units[0].error_category == "STRUCTURED_OUTPUT_INVALID"
        assert failed.failed_units == 1
        assert failed.units[0].error_category == "PROVIDER_UNAVAILABLE"
        async with sessions() as session:
            assessments = await session.scalar(
                select(func.count())
                .select_from(Assessment)
                .where(
                    Assessment.interview_session_id.in_((malformed_session_id, failed_session_id))
                )
            )
            evidence_count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id.in_((malformed_session_id, failed_session_id)))
            )
            invocation_statuses = tuple(
                await session.scalars(
                    select(AIInvocation.status).where(
                        AIInvocation.interview_session_id.in_(
                            (malformed_session_id, failed_session_id)
                        )
                    )
                )
            )
        assert assessments == 0
        assert evidence_count == 0
        assert invocation_statuses == ("FAILED", "FAILED")
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_completed_simulation_e2e_is_idempotent_and_reconstructable(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    session_id: UUID
    user_id: UUID
    concept_id: UUID
    try:
        async with sessions() as session, session.begin():
            dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
            dev.budget.max_deep_reasoning_calls = 20
            interview = dev.interview_session
            concept = await _attach_problem_concept(
                session, interview, key=f"stage5_e2e_{str(interview.id).replace('-', '_')}"
            )
            await RealtimeControlService(session).persist_candidate_code_snapshot(
                session_id=interview.id,
                message=CandidateCodeSnapshotMessage(
                    **_client(1),
                    type="candidate_code_snapshot",
                    source_code=(
                        "int left = 0; for (int right = 0; right < n; ++right) "
                        "{ if (seen[s[right]]) left = 0; }"
                    ),
                    language="cpp",
                    trigger="EDIT_BURST",
                ),
            )
            prompt = await _prompt(
                session,
                interview.id,
                kind="PROBE",
                text="Intended rationale should not replace delivery truth.",
            )
            service = RealtimeControlService(session)
            await _deliver(
                service,
                interview.id,
                prompt,
                sequence=2,
                actual="Why does resetting left break the invariant?",
            )
            await _candidate_turn(
                service,
                interview.id,
                sequence=4,
                provider_item_id="e2e-answer",
                transcript=(
                    "I incorrectly move left backward, so repeated characters re-enter the window."
                ),
            )
            await InterviewCompletionService(session).complete(
                session_id=interview.id,
                reason="USER_ENDED",
                expected_state_version=interview.state_version,
                idempotency_key="stage5-e2e-complete",
            )
            session_id = interview.id
            user_id = dev.user.id
            concept_id = concept.id
            cleanup_user_ids.add(user_id)
            cleanup_concept_ids.add(concept_id)

        concept_key = f"stage5_e2e_{str(session_id).replace('-', '_')}"
        output = _analysis_output(concept_key=concept_key, weakness=True)
        provider = FakeReasoningProvider(output_data=output)
        settings = create_settings(env_file=tmp_path / ".env")
        gateway = AIGateway(settings=settings, sessionmaker=sessions, provider=provider)
        provider.assert_no_gateway_transaction = gateway
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        )

        first = await coordinator.evaluate(session_id)
        second = await coordinator.evaluate(session_id)

        assert first.completed_units == 2
        assert first.failed_units == 0
        assert second.skipped_units == 2
        assert all(unit.error_category == "ALREADY_EVALUATED" for unit in second.units)
        assert provider.calls == 2
        async with sessions() as session:
            assessment_count = await session.scalar(
                select(func.count())
                .select_from(Assessment)
                .where(Assessment.interview_session_id == session_id)
            )
            evidence_count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id == session_id)
            )
            breakpoint_count = await session.scalar(
                select(func.count()).select_from(Breakpoint).where(Breakpoint.user_id == user_id)
            )
            snapshot = await canonical_evaluation_snapshot(session, session_id)
        assert assessment_count == 2
        assert evidence_count == 2
        assert breakpoint_count == 1
        assert snapshot["assessments"]
        assert snapshot["evidence"]
        assert snapshot["breakpoints"]
        evidence_rows = cast(list[dict[str, object]], snapshot["evidence"])
        assert {row["independence"] for row in evidence_rows} == {
            "INDEPENDENT",
            "AFTER_PROBE",
        }
        assert all(row["concept_keys"] == [concept_key] for row in evidence_rows)
        async with sessions() as session:
            evidence_id = await session.scalar(
                select(Evidence.id).where(Evidence.interview_session_id == session_id)
            )
            assert evidence_id is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceSource)
                    .where(EvidenceSource.evidence_id == evidence_id)
                )
                == 1
            )
            invocation_statuses = tuple(
                await session.scalars(
                    select(AIInvocation.status).where(
                        AIInvocation.interview_session_id == session_id
                    )
                )
            )
            assert invocation_statuses == ("SUCCEEDED", "SUCCEEDED")
            assessment_statuses = tuple(
                await session.scalars(
                    select(Assessment.status).where(Assessment.interview_session_id == session_id)
                )
            )
            assert assessment_statuses == ("VALIDATED", "VALIDATED")

        # A later immutable demonstration for the same user and canonical target
        # adds a CONTRADICTED link; it preserves both Evidence rows and leaves the
        # Breakpoint inspectable rather than declaring resolution or mastery.
        async with sessions() as session, session.begin():
            later = await create_development_interview(session, initial_stage="IMPLEMENTATION")
            later.interview_session.user_id = user_id
            cleanup_user_ids.add(later.user.id)
            later.budget.max_deep_reasoning_calls = 20
            session.add(
                ProblemConcept(
                    problem_version_id=later.problem_version.id,
                    concept_id=concept_id,
                    relevance="CORE",
                    expected_importance="HIGH",
                    role="PRIMARY",
                )
            )
            await session.flush()
            await _candidate_turn(
                RealtimeControlService(session),
                later.interview_session.id,
                sequence=1,
                provider_item_id="later-contradiction",
                transcript=(
                    "I now preserve the monotonic left boundary and can explain "
                    "why moving it backward is invalid."
                ),
            )
            await InterviewCompletionService(session).complete(
                session_id=later.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=later.interview_session.state_version,
                idempotency_key="stage5-later-contradiction-complete",
            )
            later_session_id = later.interview_session.id
        positive_output = _analysis_output(concept_key=concept_key, polarity="POSITIVE")
        positive_finding = cast(list[dict[str, Any]], positive_output["findings"])[0]
        positive_finding["breakpoint_effect"] = "CONTRADICTED"
        provider.output_data = positive_output
        contradiction = await coordinator.evaluate(later_session_id)
        assert contradiction.completed_units == 1
        async with sessions() as session:
            breakpoint = await session.scalar(
                select(Breakpoint).where(Breakpoint.user_id == user_id)
            )
            assert breakpoint is not None
            relationships = set(
                await session.scalars(
                    select(BreakpointEvidence.relationship).where(
                        BreakpointEvidence.breakpoint_id == breakpoint.id
                    )
                )
            )
            user_evidence_count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .join(InterviewSession, InterviewSession.id == Evidence.interview_session_id)
                .where(InterviewSession.user_id == user_id)
            )
        assert breakpoint.status == "OPEN"
        assert relationships == {"CREATED", "REINFORCED", "CONTRADICTED"}
        assert user_evidence_count == 3
        assert provider.calls == 3
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()

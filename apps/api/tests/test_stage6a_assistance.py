from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningRequest,
    ReasoningUsage,
)
from app.auth.models import User
from app.config.settings import create_settings
from app.db.session import build_engine
from app.evidence.assessment_schema import AssessmentFinding
from app.evidence.coordinator import (
    CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE,
    SessionEvidenceEvaluationCoordinator,
    _finding_independence_level,
)
from app.evidence.independence import IndependenceAttributionService
from app.evidence.models import Assessment, Breakpoint, BreakpointEvidence, Evidence, SkillDimension
from app.evidence.units import (
    AssessmentInputBuilder,
    AssessmentSourceFact,
    AssessmentUnit,
    AssessmentUnitKind,
)
from app.interviews.assistance import (
    AssistanceRequestCommand,
    CoachAssistanceWorkflow,
    _causal_prior_level,
    _DiagnosticTarget,
)
from app.interviews.assistance_facts import initial_final_defense_answer_captured
from app.interviews.assistance_wording import (
    COACH_ASSISTANCE_PURPOSE,
    CoachAssistanceWordingService,
)
from app.interviews.budget_policy import assistance_budget_snapshot
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.mode_policy import ModePolicy
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    SessionBudget,
)
from app.interviews.prompt_authorization import PromptAuthorizationService
from app.interviews.runtime import (
    AcceptEventCommand,
    InterviewRuntime,
    TransitionCommand,
)
from app.interviews.state_machine import TransitionContext
from app.observation.models import CodeSnapshot, InterviewEvent
from app.observation.repository import ObservationRepository
from app.problems.models import Concept, Problem, ProblemConcept
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
    RealtimeDevelopmentBootstrapRequest,
)
from app.realtime.control_service import RealtimeControlService


class FakeAssessmentProvider:
    provider_name = "fake"

    def __init__(self, output_data: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.output_data = output_data
        self.calls = 0
        self.requests: list[ReasoningRequest] = []

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        self.calls += 1
        self.requests.append(request)
        output = (
            self.output_data[min(self.calls - 1, len(self.output_data) - 1)]
            if isinstance(self.output_data, list)
            else self.output_data
        )
        return ProviderReasoningResult(
            output_data=output,
            provider=self.provider_name,
            model=model,
            provider_model_version="fake-v1",
            provider_request_id=f"request-{self.calls}",
            usage=ReasoningUsage(input_tokens=10, cached_input_tokens=0, output_tokens=10),
            latency_ms=2,
            retry_count=0,
            estimated_cost=Decimal("0.0001"),
            currency="USD",
        )


class BlockingAssistanceProvider(FakeAssessmentProvider):
    def __init__(self, *, block_purpose: str) -> None:
        super().__init__({"findings": []})
        self.block_purpose = block_purpose
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        if request.purpose == self.block_purpose:
            self.started.set()
            await self.release.wait()
        self.output_data = (
            {
                "contract_version": "coach-assistance-output.v1",
                "prompt_text": "Which invariant in the current step is least certain?",
            }
            if request.purpose == COACH_ASSISTANCE_PURPOSE
            else {"findings": []}
        )
        return await super().reason_structured(
            request, model=model, reasoning_effort=reasoning_effort
        )


async def _coach_with_transcript(
    sessions: async_sessionmaker[AsyncSession],
    *,
    stage: str = "IMPLEMENTATION",
) -> tuple[UUID, tuple[UUID, UUID, UUID]]:
    async with sessions() as session, session.begin():
        development = await create_development_interview(
            session, mode="COACH", initial_stage=stage
        )
        await RealtimeControlService(session).persist_candidate_transcript(
            session_id=development.interview_session.id,
            message=CandidateTranscriptFinalizedMessage(
                type="candidate_transcript_finalized",
                client_event_id=f"blocking-attempt-{development.interview_session.id}",
                client_instance_id="stage6a-test",
                client_sequence=1,
                provider_item_id=f"candidate-{development.interview_session.id}",
                transcript="I am testing the invariant in my current approach.",
            ),
        )
        return development.interview_session.id, (
            development.user.id,
            development.configuration.id,
            development.problem.id,
        )


def _workflow_for_provider(
    *,
    sessions: async_sessionmaker[AsyncSession],
    provider: FakeAssessmentProvider,
    tmp_path: Path,
    transaction_probe: Any | None = None,
) -> tuple[AIGateway, CoachAssistanceWorkflow]:
    gateway = AIGateway(
        settings=create_settings(env_file=tmp_path / ".env"),
        sessionmaker=sessions,
        provider=provider,
        transaction_probe=transaction_probe,
    )
    return gateway, CoachAssistanceWorkflow(
        sessionmaker=sessions,
        evidence_coordinator=SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        ),
        wording_service=CoachAssistanceWordingService(gateway),
    )


def _negative_finding(concept_key: str) -> dict[str, object]:
    return {
        "findings": [
            {
                "assessment_dimension": "CORRECTNESS",
                "polarity": "NEGATIVE",
                "confidence": 0.95,
                "technical_rationale": "The same invariant remains unsupported.",
                "evidence_finding": "The candidate repeats the same invariant error.",
                "proposed_strength": "MODERATE",
                "source_aliases": ["source_1"],
                "concept_keys": [concept_key],
                "skill_dimension_keys": ["correctness"],
                "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                "breakpoint_subtype": "left_pointer_monotonicity",
                "breakpoint_effect": "WEAKNESS",
                "breakpoint_severity": "HIGH",
            }
        ]
    }


def _wording_output(level: str) -> dict[str, object]:
    return {
        "contract_version": "coach-assistance-output.v1",
        "prompt_text": f"Continue with one bounded {level.lower()} step.",
    }


async def _deliver_assistance_prompt(
    session: AsyncSession,
    *,
    session_id: UUID,
    suffix: str,
    stage: str,
    level: str | None = None,
    prompt_id: UUID | None = None,
    target_concept_id: UUID | None = None,
    target_skill_dimension_id: UUID | None = None,
) -> tuple[InterviewerPrompt, int]:
    repository = InterviewInteractionRepository(session)
    runtime = InterviewRuntime(session)
    if prompt_id is None:
        request_event = (
            await runtime.accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                    source="SYSTEM",
                    occurred_at=datetime.now(UTC),
                    idempotency_key=f"{suffix}-request",
                    payload={"captured_stage": stage},
                )
            )
        ).event
        assert level is not None
        prompt = await repository.add_prompt(
            interview_session_id=session_id,
            origin="SYSTEM",
            kind="INSTRUCTION",
            intent=f"{level} assistance for {suffix}.",
            status="AUTHORIZED",
            assistance_type=level,
            hint_level=level,
            assistance_trigger="CANDIDATE_REQUEST",
            target_event_id=request_event.id,
            target_concept_id=target_concept_id,
            target_skill_dimension_id=target_skill_dimension_id,
            source_event_watermark=request_event.server_sequence,
            authorized_at=datetime.now(UTC),
        )
    else:
        loaded_prompt = await session.get(InterviewerPrompt, prompt_id)
        assert loaded_prompt is not None and loaded_prompt.hint_level is not None
        prompt = loaded_prompt
    delivery = await repository.add_delivery(
        interview_session_id=session_id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text=prompt.intent,
        delivery_state="STARTED",
        started_at=datetime.now(UTC),
    )
    delivery_event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type="COUNTERQ_UTTERANCE_DELIVERED",
                source="COUNTERQ_VOICE",
                occurred_at=datetime.now(UTC),
                idempotency_key=f"{suffix}-delivery",
                payload={"prompt_delivery_id": str(delivery.id)},
            )
        )
    ).event
    segment = await ObservationRepository(session).add_transcript_segment(
        session_id=session_id,
        event_id=delivery_event.id,
        speaker="COUNTERQ",
        sequence=delivery_event.server_sequence,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        text=prompt.intent,
        interview_stage=stage,
        interview_state_version=delivery_event.interview_state_version,
        delivery_state="DELIVERED",
    )
    delivery.delivery_state = "DELIVERED"
    delivery.actual_transcript_segment_id = segment.id
    delivery.completed_at = datetime.now(UTC)
    prompt.status = "DELIVERED"
    await session.flush()
    return prompt, delivery_event.server_sequence


async def _persist_candidate_answer(
    session: AsyncSession,
    *,
    session_id: UUID,
    suffix: str,
    client_sequence: int,
) -> int:
    result = await RealtimeControlService(session).persist_candidate_transcript(
        session_id=session_id,
        message=CandidateTranscriptFinalizedMessage(
            type="candidate_transcript_finalized",
            client_event_id=f"{suffix}-candidate-event",
            client_instance_id="stage6a-causal-test",
            client_sequence=client_sequence,
            provider_item_id=f"{suffix}-candidate-item",
            transcript="I still believe the same invalid invariant holds.",
        ),
    )
    return result.server_sequence


async def _candidate_response_at_sequence(
    session: AsyncSession,
    *,
    session_id: UUID,
    server_sequence: int,
) -> CandidateResponse:
    response = await session.scalar(
        select(CandidateResponse)
        .join(
            CandidateResponseSource,
            CandidateResponseSource.candidate_response_id == CandidateResponse.id,
        )
        .join(
            InterviewEvent,
            InterviewEvent.id == CandidateResponseSource.interview_event_id,
        )
        .where(
            CandidateResponse.interview_session_id == session_id,
            InterviewEvent.server_sequence == server_sequence,
        )
    )
    assert response is not None
    return response


async def _deliver_probe_prompt(
    session: AsyncSession,
    *,
    session_id: UUID,
    suffix: str,
) -> InterviewerPrompt:
    prompt = await InterviewInteractionRepository(session).add_prompt(
        interview_session_id=session_id,
        origin="SYSTEM",
        kind="PROBE",
        probe_strategy="WHY",
        intent="Why does that invariant hold?",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    service = RealtimeControlService(session)
    started = await service.start_delivery(
        session_id=session_id,
        message=CounterQDeliveryStartedMessage(
            type="counterq_delivery_started",
            client_event_id=f"{suffix}-probe-start",
            client_instance_id="stage6a-continuation-test",
            client_sequence=1,
            interviewer_prompt_id=prompt.id,
            intended_text=prompt.intent,
            provider_response_id=f"{suffix}-probe-response",
        ),
    )
    await service.complete_delivery(
        session_id=session_id,
        message=CounterQDeliveryCompletedMessage(
            type="counterq_delivery_completed",
            client_event_id=f"{suffix}-probe-complete",
            client_instance_id="stage6a-continuation-test",
            client_sequence=2,
            interviewer_prompt_id=prompt.id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id=f"{suffix}-probe-response",
            transcript=prompt.intent,
        ),
    )
    return prompt


async def test_consecutive_voice_turns_continue_same_delivered_coach_assistance(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="IMPLEMENTATION"
    )
    prompt, _delivery_sequence = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="continued-metacognitive",
        stage="IMPLEMENTATION",
        level="METACOGNITIVE",
    )

    responses: list[CandidateResponse] = []
    for index in range(3):
        sequence = await _persist_candidate_answer(
            db_session,
            session_id=development.interview_session.id,
            suffix=f"continued-answer-{index}",
            client_sequence=index + 1,
        )
        responses.append(
            await _candidate_response_at_sequence(
                db_session,
                session_id=development.interview_session.id,
                server_sequence=sequence,
            )
        )

    assert [response.interviewer_prompt_id for response in responses] == [prompt.id] * 3
    assert [response.completion_reason for response in responses] == ["COMPLETE"] * 3
    for response in responses:
        attribution = await IndependenceAttributionService(db_session).for_response(response)
        assert attribution.level == "AFTER_LIGHT_GUIDANCE"
        assert attribution.reason == "ACTUAL_ASSISTANCE_DELIVERY"


async def test_new_response_bearing_prompt_replaces_old_assistance_chain(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="IMPLEMENTATION"
    )
    old_prompt, _ = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="old-assistance",
        stage="IMPLEMENTATION",
        level="METACOGNITIVE",
    )
    first_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="old-assistance-answer",
        client_sequence=1,
    )
    new_prompt, _ = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="new-assistance",
        stage="IMPLEMENTATION",
        level="PROBLEM_NARROWING",
    )
    second_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="new-assistance-answer",
        client_sequence=2,
    )

    first = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=first_sequence,
    )
    second = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=second_sequence,
    )
    assert first.interviewer_prompt_id == old_prompt.id
    assert second.interviewer_prompt_id == new_prompt.id


async def test_stage_and_spontaneous_boundaries_stop_assistance_continuation(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="IMPLEMENTATION"
    )
    prompt, _ = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="bounded-assistance",
        stage="IMPLEMENTATION",
        level="METACOGNITIVE",
    )
    first_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="bounded-answer-a",
        client_sequence=1,
    )
    first = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=first_sequence,
    )
    assert first.interviewer_prompt_id == prompt.id

    await InterviewRuntime(db_session).transition(
        TransitionCommand(
            session_id=development.interview_session.id,
            to_stage="TESTING_DEBUGGING",
            trigger="MEANINGFUL_TESTING",
            expected_state_version=development.interview_session.state_version,
            occurred_at=datetime.now(UTC),
            context=TransitionContext("MEANINGFUL_TESTING"),
            idempotency_key="bounded-assistance-stage-change",
        )
    )
    boundary_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="bounded-spontaneous-boundary",
        client_sequence=2,
    )
    later_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="bounded-answer-after-spontaneous",
        client_sequence=3,
    )
    boundary = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=boundary_sequence,
    )
    later = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=later_sequence,
    )
    assert boundary.interviewer_prompt_id is None
    assert boundary.completion_reason == "SPONTANEOUS"
    assert later.interviewer_prompt_id is None
    assert later.completion_reason == "SPONTANEOUS"


@pytest.mark.parametrize("mode", ["COACH", "SIMULATION"])
async def test_probe_response_semantics_do_not_gain_continuation(
    db_session: AsyncSession,
    mode: str,
) -> None:
    development = await create_development_interview(
        db_session, mode=mode, initial_stage="IMPLEMENTATION"
    )
    prompt = await _deliver_probe_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix=f"{mode.lower()}-probe-chain",
    )
    first_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix=f"{mode.lower()}-probe-answer-a",
        client_sequence=3,
    )
    second_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix=f"{mode.lower()}-probe-answer-b",
        client_sequence=4,
    )
    first = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=first_sequence,
    )
    second = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=second_sequence,
    )
    assert first.interviewer_prompt_id == prompt.id
    assert (
        await IndependenceAttributionService(db_session).for_response(first)
    ).level == "AFTER_PROBE"
    assert second.interviewer_prompt_id is None
    assert second.completion_reason == "SPONTANEOUS"


async def test_continued_assistance_keeps_finding_level_target_scope(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="IMPLEMENTATION"
    )
    target = Concept(
        canonical_key=f"stage6a_continued_target_{development.interview_session.id.hex}",
        display_name="Continued target",
        category="algorithm",
        status="ACTIVE",
        description="Target for continued assistance",
    )
    unrelated = Concept(
        canonical_key=f"stage6a_continued_unrelated_{development.interview_session.id.hex}",
        display_name="Continued unrelated",
        category="algorithm",
        status="ACTIVE",
        description="Unrelated finding target",
    )
    db_session.add_all([target, unrelated])
    await db_session.flush()
    db_session.add_all(
        [
            ProblemConcept(
                problem_version_id=development.problem_version.id,
                concept_id=concept.id,
                relevance="CORE",
                expected_importance="HIGH",
                role="PRIMARY",
            )
            for concept in (target, unrelated)
        ]
    )
    skill = await db_session.scalar(
        select(SkillDimension).where(
            SkillDimension.canonical_key == "complexity_reasoning"
        )
    )
    assert skill is not None
    prompt, _ = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="targeted-continuation",
        stage="IMPLEMENTATION",
        level="METACOGNITIVE",
        target_concept_id=target.id,
        target_skill_dimension_id=skill.id,
    )
    await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="targeted-continuation-a",
        client_sequence=1,
    )
    second_sequence = await _persist_candidate_answer(
        db_session,
        session_id=development.interview_session.id,
        suffix="targeted-continuation-b",
        client_sequence=2,
    )
    response = await _candidate_response_at_sequence(
        db_session,
        session_id=development.interview_session.id,
        server_sequence=second_sequence,
    )
    assert response.interviewer_prompt_id == prompt.id
    units = await AssessmentInputBuilder(db_session).build_active_checkpoint(
        development.interview_session.id
    )
    unit = next(item for item in units if item.candidate_response_id == response.id)
    assert unit.independence_level == "AFTER_LIGHT_GUIDANCE"

    def finding(concept_key: str) -> AssessmentFinding:
        return AssessmentFinding(
            assessment_dimension="CORRECTNESS",
            polarity="POSITIVE",
            confidence=0.9,
            technical_rationale="The response supports the scoped finding.",
            evidence_finding="The candidate explained the complexity boundary.",
            proposed_strength="MODERATE",
            source_aliases=["source_1"],
            concept_keys=[concept_key],
            skill_dimension_keys=[skill.canonical_key],
            boundary_kind="NONE",
            breakpoint_subtype=None,
            breakpoint_effect="NONE",
            breakpoint_severity=None,
        )

    assert (
        await _finding_independence_level(db_session, unit, finding(target.canonical_key))
        == "AFTER_LIGHT_GUIDANCE"
    )
    assert (
        await _finding_independence_level(db_session, unit, finding(unrelated.canonical_key))
        == "INDEPENDENT"
    )


def test_mode_policy_centralizes_simulation_and_coach_budgets() -> None:
    policy = ModePolicy()
    assert policy.assistance_budget("SIMULATION").max_assistance_interventions == 0
    coach = policy.assistance_budget("COACH")
    assert (
        coach.max_assistance_interventions,
        coach.max_structural_hints,
        coach.max_direct_teaching_interventions,
        coach.max_guided_retries,
    ) == (6, 2, 1, 2)
    assert policy.assistance_request_allowed("SIMULATION") is False
    assert policy.assistance_request_allowed("COACH") is True
    assert policy.factual_clarification_allowed("SIMULATION") is True
    assert policy.factual_clarification_allowed("COACH", solution_directed=True) is False
    assert policy.solution_guidance_is_assistance() is True
    assert policy.guided_retry_allowed(mode="COACH", remaining_budget=1) is True
    assert policy.guided_retry_allowed(mode="SIMULATION", remaining_budget=1) is False
    assert (
        policy.correctness_confirmation_allowed(
            mode="SIMULATION", sufficient_independent_evidence=True
        )
        is False
    )


def test_final_defense_normal_uses_full_progressive_ladder() -> None:
    policy = ModePolicy()
    expected = (
        (None, "METACOGNITIVE"),
        ("METACOGNITIVE", "PROBLEM_NARROWING"),
        ("PROBLEM_NARROWING", "CONCEPTUAL_HINT"),
        ("CONCEPTUAL_HINT", "STRUCTURAL_HINT"),
        ("STRUCTURAL_HINT", "DIRECT_TEACHING"),
    )
    for prior, next_level in expected:
        decision = policy.evaluate_assistance(
            mode="COACH",
            stage="FINAL_DEFENSE",
            time_pressure="NORMAL",
            meaningful_attempt_exists=True,
            gap_evidence_exists=True,
            highest_delivered_level=prior,
            initial_final_defense_answer_captured=True,
        )
        assert decision.allowed is True
        assert decision.next_hint_level == next_level
        assert decision.maximum_hint_level == "DIRECT_TEACHING"
    assert policy.direct_teaching_allowed(
        mode="COACH",
        stage="FINAL_DEFENSE",
        time_pressure="NORMAL",
        gap_evidence_exists=True,
        prior_lower_level_assistance_failed=True,
    )

    constrained = policy.evaluate_assistance(
        mode="COACH",
        stage="FINAL_DEFENSE",
        time_pressure="CONSTRAINED",
        meaningful_attempt_exists=True,
        gap_evidence_exists=True,
        highest_delivered_level="PROBLEM_NARROWING",
        initial_final_defense_answer_captured=True,
    )
    assert constrained.allowed is True
    assert constrained.next_hint_level == "CONCEPTUAL_HINT"
    capped = policy.evaluate_assistance(
        mode="COACH",
        stage="FINAL_DEFENSE",
        time_pressure="CONSTRAINED",
        meaningful_attempt_exists=True,
        gap_evidence_exists=True,
        highest_delivered_level="CONCEPTUAL_HINT",
        initial_final_defense_answer_captured=True,
    )
    assert capped.allowed is False
    assert capped.reason == "TIME_PRESSURE_CAP_REACHED"
    for pressure in ("DEFENSE_RESERVED", "WRAP_ONLY"):
        protected = policy.evaluate_assistance(
            mode="COACH",
            stage="FINAL_DEFENSE",
            time_pressure=pressure,  # type: ignore[arg-type]
            meaningful_attempt_exists=True,
            gap_evidence_exists=True,
            highest_delivered_level=None,
            initial_final_defense_answer_captured=True,
        )
        assert protected.allowed is False
        assert protected.reason == f"{pressure}_PROHIBITS_ASSISTANCE"


def test_bootstrap_cannot_switch_mode_during_restoration() -> None:
    with pytest.raises(ValueError, match="mode cannot be changed"):
        RealtimeDevelopmentBootstrapRequest(
            interview_session_id="01990a11-0000-7000-8000-000000000001",
            mode="COACH",
        )


async def test_development_factory_persists_mode_specific_assistance_budgets(
    db_session: AsyncSession,
) -> None:
    simulation = await create_development_interview(db_session, mode="SIMULATION")
    coach = await create_development_interview(db_session, mode="COACH")
    assert simulation.configuration.mode == "SIMULATION"
    assert simulation.budget.max_assistance_interventions == 0
    assert coach.configuration.mode == "COACH"
    assert coach.budget.max_assistance_interventions == 6
    assert coach.budget.max_structural_hints == 2
    assert coach.budget.max_direct_teaching_interventions == 1
    assert coach.budget.max_guided_retries == 2
    assert coach.budget.max_probes == simulation.budget.max_probes == 5
    assert coach.budget.max_deep_reasoning_calls == 24
    assert coach.budget.reserved_post_interview_deep_reasoning_calls == 16


@pytest.mark.parametrize("mode", ["SIMULATION", "COACH"])
async def test_clarification_and_probe_accounting_are_shared_across_modes(
    db_session: AsyncSession, mode: str
) -> None:
    development = await create_development_interview(
        db_session, mode=mode, initial_stage="IMPLEMENTATION"
    )
    interactions = InterviewInteractionRepository(db_session)
    service = RealtimeControlService(db_session)
    clarification = await interactions.add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="CLARIFICATION",
        intent="The input values are integers.",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    clarification_started = await service.start_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            type="counterq_delivery_started",
            client_event_id=f"{mode}-clarification-start",
            client_instance_id="stage6a-test",
            client_sequence=1,
            interviewer_prompt_id=clarification.id,
            intended_text=clarification.intent,
            provider_response_id=f"{mode}-clarification-response",
        ),
    )
    await service.complete_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryCompletedMessage(
            type="counterq_delivery_completed",
            client_event_id=f"{mode}-clarification-complete",
            client_instance_id="stage6a-test",
            client_sequence=2,
            interviewer_prompt_id=clarification.id,
            prompt_delivery_id=clarification_started.delivery_id,
            provider_response_id=f"{mode}-clarification-response",
            transcript=clarification.intent,
        ),
    )
    assert development.budget.probes_used == 0
    assert development.budget.assistance_interventions_used == 0

    probe = await interactions.add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="PROBE",
        probe_strategy="WHY",
        intent="Why does that invariant hold?",
        status="AUTHORIZED",
        authorized_at=datetime.now(UTC),
    )
    probe_started = await service.start_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            type="counterq_delivery_started",
            client_event_id=f"{mode}-probe-start",
            client_instance_id="stage6a-test",
            client_sequence=3,
            interviewer_prompt_id=probe.id,
            intended_text=probe.intent,
            provider_response_id=f"{mode}-probe-response",
        ),
    )
    await service.complete_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryCompletedMessage(
            type="counterq_delivery_completed",
            client_event_id=f"{mode}-probe-complete",
            client_instance_id="stage6a-test",
            client_sequence=4,
            interviewer_prompt_id=probe.id,
            prompt_delivery_id=probe_started.delivery_id,
            provider_response_id=f"{mode}-probe-response",
            transcript=probe.intent,
        ),
    )
    assert development.budget.probes_used == 1
    assert development.budget.assistance_interventions_used == 0


async def test_assistance_instruction_requires_complete_metadata(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await InterviewInteractionRepository(db_session).add_prompt(
                interview_session_id=development.interview_session.id,
                origin="SYSTEM",
                kind="INSTRUCTION",
                intent="Invalid unclassified instruction.",
                status="AUTHORIZED",
                assistance_type="METACOGNITIVE",
            )
    legacy_instruction = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="A non-assistance system instruction remains valid.",
        status="AUTHORIZED",
    )
    assert legacy_instruction.assistance_type is None


async def test_authorized_assistance_reserves_capacity_and_charges_only_actual_delivery(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    runtime = InterviewRuntime(db_session)
    event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="budget-request",
                payload={"trigger": "CANDIDATE_REQUEST"},
            )
        )
    ).event
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="What invariant is uncertain?",
        status="AUTHORIZED",
        assistance_type="METACOGNITIVE",
        hint_level="METACOGNITIVE",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=event.id,
        source_event_watermark=event.server_sequence,
        authorized_at=datetime.now(UTC),
    )
    reserved = await assistance_budget_snapshot(db_session, development.interview_session.id)
    assert reserved is not None
    assert reserved.outstanding_assistance_interventions == 1
    assert reserved.assistance_interventions_used == 0

    delivery = await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text=prompt.intent,
        delivery_state="CANCELLED",
        started_at=datetime.now(UTC),
    )
    await PromptAuthorizationService(db_session).consume_assistance_budget_for_delivered_prompt(
        prompt
    )
    assert development.budget.assistance_interventions_used == 0

    delivered_event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="COUNTERQ_UTTERANCE_DELIVERED",
                source="COUNTERQ_VOICE",
                occurred_at=datetime.now(UTC),
                idempotency_key="budget-delivered",
            )
        )
    ).event
    segment = await ObservationRepository(db_session).add_transcript_segment(
        session_id=development.interview_session.id,
        event_id=delivered_event.id,
        speaker="COUNTERQ",
        sequence=delivered_event.server_sequence,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        text="What invariant is uncertain?",
        interview_stage=development.interview_session.current_stage,
        interview_state_version=development.interview_session.state_version,
        delivery_state="DELIVERED",
    )
    delivery.delivery_state = "DELIVERED"
    delivery.actual_transcript_segment_id = segment.id
    delivery.completed_at = datetime.now(UTC)
    await PromptAuthorizationService(db_session).consume_assistance_budget_for_delivered_prompt(
        prompt
    )
    assert development.budget.assistance_interventions_used == 1
    assert development.budget.probes_used == 0
    prompt.status = "DELIVERED"
    await PromptAuthorizationService(db_session).consume_assistance_budget_for_delivered_prompt(
        prompt
    )
    assert development.budget.assistance_interventions_used == 1

    service = RealtimeControlService(db_session)
    empty_event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="empty-interruption-request",
            )
        )
    ).event
    empty_prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="What remains uncertain?",
        status="AUTHORIZED",
        assistance_type="METACOGNITIVE",
        hint_level="METACOGNITIVE",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=empty_event.id,
        source_event_watermark=empty_event.server_sequence,
        authorized_at=datetime.now(UTC),
    )
    empty_started = await service.start_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            type="counterq_delivery_started",
            client_event_id="empty-interruption-start",
            client_instance_id="stage6a-test",
            client_sequence=3,
            interviewer_prompt_id=empty_prompt.id,
            intended_text=empty_prompt.intent,
            provider_response_id="empty-interruption-response",
        ),
    )
    empty_interrupted = await service.interrupt_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryInterruptedMessage(
            type="counterq_delivery_interrupted",
            client_event_id="empty-interruption-finish",
            client_instance_id="stage6a-test",
            client_sequence=4,
            interviewer_prompt_id=empty_prompt.id,
            prompt_delivery_id=empty_started.delivery_id,
            provider_response_id="empty-interruption-response",
            confirmed_by="candidate_speech",
            transcript=None,
        ),
    )
    assert empty_interrupted.delivery_state == "INTERRUPTED"
    assert empty_interrupted.transcript_segment_id is None
    assert development.budget.assistance_interventions_used == 1


async def test_proposed_assistance_reserves_capacity_without_consuming_it(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="proposed-budget-request",
            )
        )
    ).event
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="[pending]",
        status="PROPOSED",
        assistance_type="METACOGNITIVE",
        hint_level="METACOGNITIVE",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=event.id,
        source_event_watermark=event.server_sequence,
    )
    reserved = await assistance_budget_snapshot(db_session, development.interview_session.id)
    assert reserved is not None
    assert reserved.outstanding_assistance_interventions == 1
    assert reserved.assistance_interventions_used == 0
    permit = await PromptAuthorizationService(db_session).permit_delivery(
        session_id=development.interview_session.id,
        prompt_id=prompt.id,
    )
    assert permit.status == "REJECTED"

    prompt.status = "REJECTED"
    released = await assistance_budget_snapshot(db_session, development.interview_session.id)
    assert released is not None
    assert released.outstanding_assistance_interventions == 0
    assert released.assistance_interventions_used == 0


async def test_sequence_causality_escalates_only_same_target_post_delivery(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    concept = Concept(
        canonical_key=f"stage6a_sequence_{development.interview_session.id.hex}",
        display_name="Sequence target",
        category="algorithm",
        status="ACTIVE",
        description="Sequence-causal assistance target",
    )
    db_session.add(concept)
    skill = await db_session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == "correctness")
    )
    assert skill is not None
    request_event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="sequence-causal-request",
            )
        )
    ).event
    await db_session.flush()
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="Which invariant is uncertain?",
        status="AUTHORIZED",
        assistance_type="METACOGNITIVE",
        hint_level="METACOGNITIVE",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=request_event.id,
        target_concept_id=concept.id,
        target_skill_dimension_id=skill.id,
        source_event_watermark=request_event.server_sequence,
        authorized_at=datetime.now(UTC),
    )
    delivery_event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="COUNTERQ_UTTERANCE_DELIVERED",
                source="COUNTERQ_VOICE",
                occurred_at=datetime.now(UTC),
                idempotency_key="sequence-causal-delivery",
            )
        )
    ).event
    segment = await ObservationRepository(db_session).add_transcript_segment(
        session_id=development.interview_session.id,
        event_id=delivery_event.id,
        speaker="COUNTERQ",
        sequence=delivery_event.server_sequence,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        text=prompt.intent,
        interview_stage=development.interview_session.current_stage,
        interview_state_version=development.interview_session.state_version,
        delivery_state="DELIVERED",
    )
    await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text=prompt.intent,
        actual_transcript_segment_id=segment.id,
        delivery_state="DELIVERED",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    def target(*, concept_id: UUID, watermark: int) -> _DiagnosticTarget:
        return _DiagnosticTarget(
            evidence_id=uuid4(),
            concept_id=concept_id,
            concept_key="sequence_target",
            skill_dimension_id=skill.id,
            skill_dimension_key="correctness",
            finding="Current gap",
            boundary="sequence_boundary",
            polarity="NEGATIVE",
            strength="STRONG",
            confidence=Decimal("0.99"),
            source_watermark=watermark,
        )

    pre_level, pre_stable = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=delivery_event.server_sequence),
    )
    assert pre_level is None and pre_stable is True
    post_level, post_stable = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=delivery_event.server_sequence + 1),
    )
    assert post_level == "METACOGNITIVE" and post_stable is False
    unrelated_level, unrelated_stable = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=uuid4(), watermark=delivery_event.server_sequence + 1),
    )
    assert unrelated_level is None and unrelated_stable is False
    assert ModePolicy.next_level(post_level) == "PROBLEM_NARROWING"
    assert ModePolicy.next_level(unrelated_level) == "METACOGNITIVE"
    defense_level, defense_stable = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=delivery_event.server_sequence + 1),
        delivery_stage="FINAL_DEFENSE",
    )
    assert defense_level is None and defense_stable is False

    first_failure = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="MEANINGFUL_CODE_CHANGE",
                source="NATIVE_EDITOR",
                occurred_at=datetime.now(UTC),
                idempotency_key="sequence-causal-first-failure",
            )
        )
    ).event
    causal_l1, _ = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=first_failure.server_sequence),
    )
    assert causal_l1 == "METACOGNITIVE"
    _, l2_delivery_sequence = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix="sequence-causal-l2",
        stage=development.interview_session.current_stage,
        level="PROBLEM_NARROWING",
        target_concept_id=concept.id,
        target_skill_dimension_id=skill.id,
    )
    assert l2_delivery_sequence > first_failure.server_sequence
    untested_l2, stable_after_l2 = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=first_failure.server_sequence),
    )
    assert untested_l2 is None and stable_after_l2 is True

    second_failure = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="MEANINGFUL_CODE_CHANGE",
                source="NATIVE_EDITOR",
                occurred_at=datetime.now(UTC),
                idempotency_key="sequence-causal-second-failure",
            )
        )
    ).event
    causal_l2, stable_after_new_failure = await _causal_prior_level(
        db_session,
        development.interview_session.id,
        target(concept_id=concept.id, watermark=second_failure.server_sequence),
    )
    assert causal_l2 == "PROBLEM_NARROWING"
    assert stable_after_new_failure is False
    assert ModePolicy.next_level(causal_l2) == "CONCEPTUAL_HINT"


@pytest.mark.parametrize(
    ("prompt_scope", "expected_relevant"),
    (
        ("CONCEPT_ONLY", True),
        ("SKILL_ONLY", True),
        ("BOTH_DIFFERENT_SKILL", False),
        ("UNRELATED_CONCEPT", False),
    ),
)
async def test_causal_assistance_target_matching_uses_populated_dimensions(
    db_session: AsyncSession,
    prompt_scope: str,
    expected_relevant: bool,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    concept = Concept(
        canonical_key=f"stage6a_scope_{prompt_scope}_{development.interview_session.id.hex}",
        display_name="Current concept",
        category="algorithm",
        status="ACTIVE",
        description="Current causal target",
    )
    other_concept = Concept(
        canonical_key=f"stage6a_other_{prompt_scope}_{development.interview_session.id.hex}",
        display_name="Other concept",
        category="algorithm",
        status="ACTIVE",
        description="Unrelated causal target",
    )
    db_session.add_all([concept, other_concept])
    await db_session.flush()
    correctness = await db_session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == "correctness")
    )
    debugging = await db_session.scalar(
        select(SkillDimension).where(SkillDimension.canonical_key == "debugging")
    )
    assert correctness is not None and debugging is not None
    prompt_concept_id = (
        None
        if prompt_scope == "SKILL_ONLY"
        else other_concept.id
        if prompt_scope == "UNRELATED_CONCEPT"
        else concept.id
    )
    prompt_skill_id = (
        correctness.id
        if prompt_scope == "SKILL_ONLY"
        else debugging.id
        if prompt_scope == "BOTH_DIFFERENT_SKILL"
        else None
    )
    _, delivery_sequence = await _deliver_assistance_prompt(
        db_session,
        session_id=development.interview_session.id,
        suffix=f"target-scope-{prompt_scope.lower()}",
        stage=development.interview_session.current_stage,
        level="METACOGNITIVE",
        target_concept_id=prompt_concept_id,
        target_skill_dimension_id=prompt_skill_id,
    )
    target = _DiagnosticTarget(
        evidence_id=uuid4(),
        concept_id=concept.id,
        concept_key=concept.canonical_key,
        skill_dimension_id=correctness.id,
        skill_dimension_key=correctness.canonical_key,
        finding="Current same-target gap",
        boundary="target_scope",
        polarity="NEGATIVE",
        strength="MODERATE",
        confidence=Decimal("0.95"),
        source_watermark=delivery_sequence + 1,
    )
    prior, stable = await _causal_prior_level(
        db_session, development.interview_session.id, target
    )
    assert stable is False
    assert (prior == "METACOGNITIVE") is expected_relevant
    if not expected_relevant:
        assert ModePolicy.next_level(prior) == "METACOGNITIVE"


async def test_latest_untested_assistance_defers_before_wording(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            session_id = development.interview_session.id
            concept = Concept(
                canonical_key=f"stage6a_latest_{session_id.hex}",
                display_name="Latest assistance target",
                category="algorithm",
                status="ACTIVE",
                description="Newest applicable delivery controls progression",
            )
            session.add(concept)
            await session.flush()
            session.add(
                ProblemConcept(
                    problem_version_id=development.problem_version.id,
                    concept_id=concept.id,
                    relevance="PRIMARY",
                    expected_importance="HIGH",
                    role="CORE",
                )
            )
            skill = await session.scalar(
                select(SkillDimension).where(
                    SkillDimension.canonical_key == "correctness"
                )
            )
            assert skill is not None
            await _deliver_assistance_prompt(
                session,
                session_id=session_id,
                suffix="latest-untested-l1",
                stage="IMPLEMENTATION",
                level="METACOGNITIVE",
                target_concept_id=concept.id,
                target_skill_dimension_id=skill.id,
            )
            failure_sequence = await _persist_candidate_answer(
                session,
                session_id=session_id,
                suffix="latest-untested-failure",
                client_sequence=1,
            )
            concept_key = concept.canonical_key

        provider = FakeAssessmentProvider(_negative_finding(concept_key))
        gateway, workflow = _workflow_for_provider(
            sessions=sessions, provider=provider, tmp_path=tmp_path
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        )
        checkpoint = await coordinator.evaluate_active_checkpoint(session_id)
        assert checkpoint.completed_units == 1
        assert provider.calls == 1
        async with sessions() as session, session.begin():
            _, l2_delivery_sequence = await _deliver_assistance_prompt(
                session,
                session_id=session_id,
                suffix="latest-untested-l2",
                stage="IMPLEMENTATION",
                level="PROBLEM_NARROWING",
                target_concept_id=concept.id,
                target_skill_dimension_id=skill.id,
            )
            assert l2_delivery_sequence > failure_sequence

        result = await workflow.request(
            AssistanceRequestCommand(session_id, "latest-untested-next-request")
        )
        assert result.status == "DEFERRED"
        assert result.reason == "STABLE_FAILURE_OR_PROGRESS_REQUIRED"
        assert result.interviewer_prompt_id is None
        assert provider.calls == 1
        assert all(
            request.purpose != COACH_ASSISTANCE_PURPOSE for request in provider.requests
        )
        async with sessions() as session:
            reservation = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.target_event_id == result.request_event_id
                )
            )
            assert reservation is not None and reservation.status == "CANCELLED"
            authorized = await session.scalar(
                select(func.count(InterviewerPrompt.id)).where(
                    InterviewerPrompt.interview_session_id == session_id,
                    InterviewerPrompt.assistance_type.is_not(None),
                    InterviewerPrompt.status == "AUTHORIZED",
                )
            )
            assert authorized == 0
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_final_defense_workflow_progresses_to_direct_teaching(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    levels = (
        "METACOGNITIVE",
        "PROBLEM_NARROWING",
        "CONCEPTUAL_HINT",
        "STRUCTURAL_HINT",
        "DIRECT_TEACHING",
    )
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="FINAL_DEFENSE"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            # Five assessed rungs plus five wording calls require ten live
            # STANDARD_REASONING slots; expand only this test session so every
            # approved assistance budget is demonstrably available.
            development.budget.max_deep_reasoning_calls += 2
            session_id = development.interview_session.id
            concept = Concept(
                canonical_key=f"stage6a_defense_ladder_{session_id.hex}",
                display_name="Final Defense ladder target",
                category="algorithm",
                status="ACTIVE",
                description="Final Defense progressive Coach target",
            )
            session.add(concept)
            await session.flush()
            session.add(
                ProblemConcept(
                    problem_version_id=development.problem_version.id,
                    concept_id=concept.id,
                    relevance="PRIMARY",
                    expected_importance="HIGH",
                    role="CORE",
                )
            )
            service = RealtimeControlService(session)
            await service.persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="defense-independent-code-initial",
                    client_instance_id="stage6a-final-defense",
                    client_sequence=1,
                    source_code="int answer = 0;",
                    language="cpp",
                    trigger="INITIAL_EDITOR_STATE",
                ),
            )
            await service.persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="defense-independent-code-attempt",
                    client_instance_id="stage6a-final-defense",
                    client_sequence=2,
                    source_code="int answer = 1;",
                    language="cpp",
                    trigger="EDIT_BURST",
                ),
            )
            concept_key = concept.canonical_key

        outputs: list[dict[str, object]] = []
        for level in levels:
            outputs.extend((_negative_finding(concept_key), _wording_output(level)))
        provider = FakeAssessmentProvider(outputs)
        _gateway, workflow = _workflow_for_provider(
            sessions=sessions, provider=provider, tmp_path=tmp_path
        )
        before_answer = await workflow.request(
            AssistanceRequestCommand(session_id, "defense-before-independent-answer")
        )
        assert before_answer.status == "DENIED"
        assert before_answer.reason == "FINAL_DEFENSE_INITIAL_ANSWER_REQUIRED"
        assert provider.calls == 0

        async with sessions() as session, session.begin():
            await _persist_candidate_answer(
                session,
                session_id=session_id,
                suffix="defense-initial-answer",
                client_sequence=1,
            )

        authorized_ids: list[UUID] = []
        for index, expected_level in enumerate(levels):
            result = await workflow.request(
                AssistanceRequestCommand(session_id, f"defense-ladder-request-{index}")
            )
            assert result.status == "AUTHORIZED", result
            assert result.hint_level == expected_level
            assert result.interviewer_prompt_id is not None
            authorized_ids.append(result.interviewer_prompt_id)
            if expected_level == "DIRECT_TEACHING":
                break
            async with sessions() as session, session.begin():
                await _deliver_assistance_prompt(
                    session,
                    session_id=session_id,
                    suffix=f"defense-ladder-{index}",
                    stage="FINAL_DEFENSE",
                    prompt_id=result.interviewer_prompt_id,
                )
                await _persist_candidate_answer(
                    session,
                    session_id=session_id,
                    suffix=f"defense-post-hint-failure-{index}",
                    client_sequence=index + 2,
                )

        assert len(authorized_ids) == len(levels)
        assert provider.calls == len(levels) * 2
        assert sum(
            request.purpose == COACH_ASSISTANCE_PURPOSE for request in provider.requests
        ) == len(levels)
        async with sessions() as session:
            evidence = list(
                await session.scalars(
                    select(Evidence).where(Evidence.interview_session_id == session_id)
                )
            )
            assert any(item.independence_level == "INDEPENDENT" for item in evidence)
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_final_defense_answer_is_derived_from_durable_response(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="FINAL_DEFENSE"
    )
    session_id = development.interview_session.id
    assert not await initial_final_defense_answer_captured(db_session, session_id)
    persisted = await RealtimeControlService(db_session).persist_candidate_transcript(
        session_id=session_id,
        message=CandidateTranscriptFinalizedMessage(
            type="candidate_transcript_finalized",
            client_event_id="final-defense-answer",
            client_instance_id="stage6a-test",
            client_sequence=1,
            provider_item_id="candidate-final-defense-answer",
            transcript="The left boundary is monotonic because each value exits once.",
        ),
    )
    assert await initial_final_defense_answer_captured(db_session, session_id)
    assert await initial_final_defense_answer_captured(
        db_session,
        session_id,
        before_sequence=persisted.server_sequence + 1,
    )


async def test_target_scoped_assistance_does_not_contaminate_unrelated_finding(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    first = Concept(
        canonical_key=f"stage6a_first_{development.interview_session.id.hex}",
        display_name="First",
        category="algorithm",
        status="ACTIVE",
        description="First target",
    )
    second = Concept(
        canonical_key=f"stage6a_second_{development.interview_session.id.hex}",
        display_name="Second",
        category="algorithm",
        status="ACTIVE",
        description="Second target",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="target-request",
            )
        )
    ).event
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="Focus on the first target.",
        status="DELIVERED",
        assistance_type="CONCEPTUAL_HINT",
        hint_level="CONCEPTUAL_HINT",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=event.id,
        source_event_watermark=event.server_sequence,
        target_concept_id=first.id,
    )
    response = await InterviewInteractionRepository(db_session).add_response(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        completion_reason="COMPLETE",
    )
    unit = AssessmentUnit(
        unit_key="sha256:" + "a" * 64,
        kind=AssessmentUnitKind.PROMPTED_RESPONSE,
        interview_session_id=development.interview_session.id,
        sort_sequence=event.server_sequence,
        sources=(
            AssessmentSourceFact(
                alias="source_1",
                event_id=event.id,
                server_sequence=event.server_sequence,
                event_type=event.event_type,
                event_source=event.source,
                source_role="PRIMARY",
            ),
        ),
        independence_level="AFTER_LIGHT_GUIDANCE",
        independence_reason="ACTUAL_ASSISTANCE_DELIVERY",
        candidate_response_id=response.id,
        source_code_snapshot_id=None,
        concept_ids_by_key={"first": first.id, "second": second.id},
        skill_ids_by_key={},
        input_payload={},
    )
    finding = AssessmentFinding(
        assessment_dimension="CORRECTNESS",
        polarity="POSITIVE",
        confidence=0.9,
        technical_rationale="The second concept is independently supported.",
        evidence_finding="The candidate demonstrated the second concept.",
        proposed_strength="MODERATE",
        source_aliases=["source_1"],
        concept_keys=["second"],
        skill_dimension_keys=[],
        boundary_kind="NONE",
        breakpoint_subtype=None,
        breakpoint_effect="NONE",
        breakpoint_severity=None,
    )
    assert await _finding_independence_level(db_session, unit, finding) == "INDEPENDENT"


async def test_target_scoped_assistance_does_not_contaminate_unrelated_direct_code(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    first = Concept(
        canonical_key=f"stage6a_direct_first_{development.interview_session.id.hex}",
        display_name="First direct target",
        category="algorithm",
        status="ACTIVE",
        description="Assisted target",
    )
    second = Concept(
        canonical_key=f"stage6a_direct_second_{development.interview_session.id.hex}",
        display_name="Second direct target",
        category="algorithm",
        status="ACTIVE",
        description="Independent target",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    runtime = InterviewRuntime(db_session)
    request_event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="direct-target-request",
            )
        )
    ).event
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="Focus on the first direct target.",
        status="DELIVERED",
        assistance_type="CONCEPTUAL_HINT",
        hint_level="CONCEPTUAL_HINT",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=request_event.id,
        source_event_watermark=request_event.server_sequence,
        target_concept_id=first.id,
        authorized_at=datetime.now(UTC),
    )
    delivery = await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text=prompt.intent,
        delivery_state="STARTED",
        started_at=datetime.now(UTC),
    )
    delivery_event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="COUNTERQ_UTTERANCE_DELIVERED",
                source="COUNTERQ_VOICE",
                occurred_at=datetime.now(UTC),
                idempotency_key="direct-target-delivery",
                payload={"prompt_delivery_id": str(delivery.id)},
            )
        )
    ).event
    segment = await ObservationRepository(db_session).add_transcript_segment(
        session_id=development.interview_session.id,
        event_id=delivery_event.id,
        speaker="COUNTERQ",
        sequence=delivery_event.server_sequence,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        text=prompt.intent,
        interview_stage=development.interview_session.current_stage,
        interview_state_version=development.interview_session.state_version,
        delivery_state="DELIVERED",
    )
    delivery.delivery_state = "DELIVERED"
    delivery.actual_transcript_segment_id = segment.id
    delivery.completed_at = datetime.now(UTC)
    code_event = (
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="MEANINGFUL_CODE_CHANGE",
                source="NATIVE_EDITOR",
                occurred_at=datetime.now(UTC),
                idempotency_key="direct-target-code",
            )
        )
    ).event
    unit = AssessmentUnit(
        unit_key="sha256:" + "b" * 64,
        kind=AssessmentUnitKind.DIRECT_CODE,
        interview_session_id=development.interview_session.id,
        sort_sequence=code_event.server_sequence,
        sources=(
            AssessmentSourceFact(
                alias="source_1",
                event_id=code_event.id,
                server_sequence=code_event.server_sequence,
                event_type=code_event.event_type,
                event_source=code_event.source,
                source_role="PRIMARY",
            ),
        ),
        independence_level="AFTER_LIGHT_GUIDANCE",
        independence_reason="STRONGEST_ACTUAL_PROMPT_INFLUENCE",
        candidate_response_id=None,
        source_code_snapshot_id=None,
        concept_ids_by_key={"first": first.id, "second": second.id},
        skill_ids_by_key={},
        input_payload={},
    )
    finding = AssessmentFinding(
        assessment_dimension="CORRECTNESS",
        polarity="POSITIVE",
        confidence=0.9,
        technical_rationale="The second concept is supported by code.",
        evidence_finding="The code demonstrates the unrelated second concept.",
        proposed_strength="MODERATE",
        source_aliases=["source_1"],
        concept_keys=["second"],
        skill_dimension_keys=[],
        boundary_kind="NONE",
        breakpoint_subtype=None,
        breakpoint_effect="NONE",
        breakpoint_severity=None,
    )
    assert await _finding_independence_level(db_session, unit, finding) == "INDEPENDENT"


async def test_partial_assistance_delivery_consumes_once_and_records_heard_text(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session, mode="COACH", initial_stage="IMPLEMENTATION"
    )
    event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                source="SYSTEM",
                occurred_at=datetime.now(UTC),
                idempotency_key="partial-assistance-request",
            )
        )
    ).event
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="Trace one concrete case, then retry.",
        status="AUTHORIZED",
        assistance_type="STRUCTURAL_HINT",
        hint_level="STRUCTURAL_HINT",
        assistance_trigger="CANDIDATE_REQUEST",
        target_event_id=event.id,
        source_event_watermark=event.server_sequence,
        invites_guided_retry=True,
        authorized_at=datetime.now(UTC),
    )
    service = RealtimeControlService(db_session)
    started = await service.start_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            type="counterq_delivery_started",
            client_event_id="partial-start",
            client_instance_id="stage6a-test",
            client_sequence=1,
            interviewer_prompt_id=prompt.id,
            intended_text="Browser text is not authoritative for assistance.",
            provider_response_id="partial-response",
        ),
    )
    interrupted = await service.interrupt_delivery(
        session_id=development.interview_session.id,
        message=CounterQDeliveryInterruptedMessage(
            type="counterq_delivery_interrupted",
            client_event_id="partial-interrupt",
            client_instance_id="stage6a-test",
            client_sequence=2,
            interviewer_prompt_id=prompt.id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="partial-response",
            confirmed_by="candidate_speech",
            transcript="Trace one concrete case",
        ),
    )
    assert interrupted.delivery_state == "PARTIALLY_DELIVERED"
    assert interrupted.transcript_segment_id is not None
    assert development.budget.assistance_interventions_used == 1
    assert development.budget.structural_hints_used == 1
    assert development.budget.guided_retries_used == 1
    delivery = await db_session.get(InterviewerPromptDelivery, started.delivery_id)
    assert delivery is not None
    assert delivery.intended_text == prompt.intent
    response = await InterviewInteractionRepository(db_session).add_response(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        completion_reason="COMPLETE",
    )
    attribution = await IndependenceAttributionService(db_session).for_response(response)
    assert attribution.level == "AFTER_STRONG_HINT"
    await PromptAuthorizationService(db_session).consume_assistance_budget_for_delivered_prompt(
        prompt
    )
    assert development.budget.assistance_interventions_used == 1


async def test_simulation_request_persists_refusal_without_assistance_metadata(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="SIMULATION", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            session_id = development.interview_session.id
        result = await CoachAssistanceWorkflow(sessionmaker=sessions).request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="simulation-hint",
            )
        )
        assert result.status == "REFUSED"
        assert result.hint_level is None
        retry = await CoachAssistanceWorkflow(sessionmaker=sessions).request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="simulation-hint",
            )
        )
        assert retry.interviewer_prompt_id == result.interviewer_prompt_id
        assert retry.status == "REFUSED"
        async with sessions() as session:
            prompt = await session.get(InterviewerPrompt, result.interviewer_prompt_id)
            assert prompt is not None
            assert prompt.kind == "CLARIFICATION"
            assert prompt.assistance_type is None
            assert result.budget.assistance_interventions_used == 0
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_coach_request_requires_attempt_and_defers_without_provider() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            session_id = development.interview_session.id
        workflow = CoachAssistanceWorkflow(sessionmaker=sessions)
        before_attempt = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-before-attempt",
            )
        )
        assert before_attempt.status == "ATTEMPT_REQUIRED"
        assert before_attempt.assistance_type is None
        before_attempt_retry = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-before-attempt",
            )
        )
        assert before_attempt_retry.interviewer_prompt_id == before_attempt.interviewer_prompt_id

        async with sessions() as session, session.begin():
            await InterviewRuntime(session).accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="MEANINGFUL_CODE_CHANGE",
                    source="NATIVE_EDITOR",
                    occurred_at=datetime.now(UTC),
                    idempotency_key="coach-meaningful-attempt",
                )
            )
        historical_retry = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-before-attempt",
            )
        )
        assert historical_retry.status == "ATTEMPT_REQUIRED"
        assert historical_retry.interviewer_prompt_id == before_attempt.interviewer_prompt_id
        deferred = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-after-attempt",
            )
        )
        assert deferred.status == "DEFERRED"
        assert deferred.reason == "ASSISTANCE_PROVIDER_UNAVAILABLE"
        assert deferred.interviewer_prompt_id is None
        assert deferred.request_event_watermark > 0
        assert deferred.budget.outstanding_assistance_interventions == 0

        retry = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-after-attempt",
            )
        )
        assert retry.interviewer_prompt_id is None
        assert retry.reason == "ASSISTANCE_PROVIDER_UNAVAILABLE"
        concurrent = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-concurrent-request",
            )
        )
        assert concurrent.interviewer_prompt_id is None
        assert concurrent.reason == "ASSISTANCE_PROVIDER_UNAVAILABLE"
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_candidate_progress_suppresses_authorized_assistance_before_delivery(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            session_id = development.interview_session.id
            await RealtimeControlService(session).persist_candidate_transcript(
                session_id=session_id,
                message=CandidateTranscriptFinalizedMessage(
                    type="candidate_transcript_finalized",
                    client_event_id="stale-attempt",
                    client_instance_id="stage6a-test",
                    client_sequence=1,
                    provider_item_id="candidate-stale-attempt",
                    transcript="I am uncertain about the invariant in my approach.",
                ),
            )
        provider = FakeAssessmentProvider(
            [
                {"findings": []},
                {
                    "contract_version": "coach-assistance-output.v1",
                    "prompt_text": "Which part of your current invariant feels least certain?",
                },
            ]
        )
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        result = await CoachAssistanceWorkflow(
            sessionmaker=sessions,
            evidence_coordinator=SessionEvidenceEvaluationCoordinator(
                sessionmaker=sessions, ai_gateway=gateway
            ),
            wording_service=CoachAssistanceWordingService(gateway),
        ).request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="stale-hint",
            )
        )
        assert result.status == "AUTHORIZED"
        assert result.interviewer_prompt_id is not None
        async with sessions() as session, session.begin():
            await InterviewRuntime(session).accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="MEANINGFUL_CODE_CHANGE",
                    source="NATIVE_EDITOR",
                    occurred_at=datetime.now(UTC),
                    idempotency_key="stale-self-correction",
                )
            )
        async with sessions() as session, session.begin():
            permit = await PromptAuthorizationService(session).permit_delivery(
                session_id=session_id,
                prompt_id=result.interviewer_prompt_id,
            )
            assert permit.status == "STALE"
            prompt = await session.get(InterviewerPrompt, result.interviewer_prompt_id)
            assert prompt is not None and prompt.status == "STALE"
            budget = await assistance_budget_snapshot(session, session_id)
            assert budget is not None
            assert budget.assistance_interventions_used == 0
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_candidate_progress_during_active_checkpoint_prevents_wording(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    session_id, cleanup = await _coach_with_transcript(sessions)
    provider = BlockingAssistanceProvider(
        block_purpose=CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE
    )
    _gateway, workflow = _workflow_for_provider(
        sessions=sessions, provider=provider, tmp_path=tmp_path
    )
    try:
        task = asyncio.create_task(
            workflow.request(
                AssistanceRequestCommand(session_id, "progress-during-checkpoint")
            )
        )
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session, session.begin():
            await InterviewRuntime(session).accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="MEANINGFUL_CODE_CHANGE",
                    source="NATIVE_EDITOR",
                    occurred_at=datetime.now(UTC),
                    idempotency_key="progress-arrived-during-checkpoint",
                )
            )
        provider.release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.status == "DEFERRED"
        assert result.interviewer_prompt_id is None
        assert provider.calls == 1
        assert all(request.purpose != COACH_ASSISTANCE_PURPOSE for request in provider.requests)
        async with sessions() as session:
            prompt = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.target_event_id == result.request_event_id
                )
            )
            assert prompt is not None and prompt.status == "STALE"
            budget = await assistance_budget_snapshot(session, session_id)
            assert budget is not None
            assert budget.outstanding_assistance_interventions == 0
    finally:
        await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_candidate_progress_during_wording_prevents_authorization(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    session_id, cleanup = await _coach_with_transcript(sessions)
    provider = BlockingAssistanceProvider(block_purpose=COACH_ASSISTANCE_PURPOSE)
    _gateway, workflow = _workflow_for_provider(
        sessions=sessions, provider=provider, tmp_path=tmp_path
    )
    try:
        task = asyncio.create_task(
            workflow.request(AssistanceRequestCommand(session_id, "progress-during-wording"))
        )
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session, session.begin():
            await InterviewRuntime(session).accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="TRANSCRIPT_FINALIZED",
                    source="CANDIDATE_VOICE",
                    occurred_at=datetime.now(UTC),
                    idempotency_key="voice-progress-during-wording",
                )
            )
        provider.release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.status == "DEFERRED"
        assert result.interviewer_prompt_id is None
        assert provider.calls == 2
        async with sessions() as session:
            prompt = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.target_event_id == result.request_event_id
                )
            )
            assert prompt is not None and prompt.status == "STALE"
    finally:
        await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_code_change_during_wording_stales_captured_snapshot(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        development = await create_development_interview(
            session, mode="COACH", initial_stage="IMPLEMENTATION"
        )
        cleanup = (
            development.user.id,
            development.configuration.id,
            development.problem.id,
        )
        session_id = development.interview_session.id
        service = RealtimeControlService(session)
        await service.persist_candidate_code_snapshot(
            session_id=session_id,
            message=CandidateCodeSnapshotMessage(
                type="candidate_code_snapshot",
                client_event_id="wording-code-initial",
                client_instance_id="stage6a-test",
                client_sequence=1,
                source_code="int value = 0;",
                language="cpp",
                trigger="INITIAL_EDITOR_STATE",
            ),
        )
        captured = await service.persist_candidate_code_snapshot(
            session_id=session_id,
            message=CandidateCodeSnapshotMessage(
                type="candidate_code_snapshot",
                client_event_id="wording-code-attempt",
                client_instance_id="stage6a-test",
                client_sequence=2,
                source_code="int value = 1;",
                language="cpp",
                trigger="EDIT_BURST",
            ),
        )
    provider = BlockingAssistanceProvider(block_purpose=COACH_ASSISTANCE_PURPOSE)
    _gateway, workflow = _workflow_for_provider(
        sessions=sessions, provider=provider, tmp_path=tmp_path
    )
    try:
        task = asyncio.create_task(
            workflow.request(AssistanceRequestCommand(session_id, "code-during-wording"))
        )
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        async with sessions() as session, session.begin():
            await RealtimeControlService(session).persist_candidate_code_snapshot(
                session_id=session_id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="wording-code-progress",
                    client_instance_id="stage6a-test",
                    client_sequence=3,
                    source_code="int value = 2;",
                    language="cpp",
                    trigger="EDIT_BURST",
                ),
            )
        provider.release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.status == "DEFERRED"
        assert result.interviewer_prompt_id is None
        wording_payload = json.loads(provider.requests[1].input_content)
        captured_payload = wording_payload["untrusted_candidate_context"]["content"][
            "current_code_snapshot"
        ]
        assert captured_payload["id"] == str(captured.snapshot_id)
        assert captured_payload["version_number"] == captured.version_number
        assert captured_payload["source_code"] == "int value = 1;"
        async with sessions() as session:
            prompt = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.target_event_id == result.request_event_id
                )
            )
            assert prompt is not None and prompt.status == "STALE"
    finally:
        await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_concurrent_same_request_uses_one_proposed_slot_and_wording_call(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    session_id, cleanup = await _coach_with_transcript(sessions)
    provider = BlockingAssistanceProvider(block_purpose=COACH_ASSISTANCE_PURPOSE)
    probe_values: list[bool] = []
    gateway_holder: dict[str, AIGateway] = {}

    def transaction_probe() -> bool:
        active = gateway_holder["gateway"].active_transaction_count > 0
        probe_values.append(active)
        return active

    gateway, workflow = _workflow_for_provider(
        sessions=sessions,
        provider=provider,
        tmp_path=tmp_path,
        transaction_probe=transaction_probe,
    )
    gateway_holder["gateway"] = gateway
    command = AssistanceRequestCommand(session_id, "concurrent-same-request")
    try:
        first_task = asyncio.create_task(workflow.request(command))
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        duplicate = await workflow.request(command)
        assert duplicate.status == "DEFERRED"
        assert duplicate.reason == "ASSISTANCE_GENERATION_IN_PROGRESS"
        assert duplicate.interviewer_prompt_id is None
        async with sessions() as session:
            budget = await assistance_budget_snapshot(session, session_id)
            assert budget is not None
            assert budget.outstanding_assistance_interventions == 1
            assert budget.assistance_interventions_used == 0
        provider.release.set()
        first = await asyncio.wait_for(first_task, timeout=5)
        assert first.status == "AUTHORIZED"
        assert first.interviewer_prompt_id is not None
        assert provider.calls == 2
        assert sum(
            request.purpose == COACH_ASSISTANCE_PURPOSE for request in provider.requests
        ) == 1
        assert probe_values and not any(probe_values)
    finally:
        await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_malformed_wording_rejects_reservation_and_releases_capacity(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    session_id, cleanup = await _coach_with_transcript(sessions)
    provider = FakeAssessmentProvider([{"findings": []}, {"unexpected": "shape"}])
    _gateway, workflow = _workflow_for_provider(
        sessions=sessions, provider=provider, tmp_path=tmp_path
    )
    try:
        result = await workflow.request(
            AssistanceRequestCommand(session_id, "malformed-wording")
        )
        assert result.status == "DEFERRED"
        assert result.reason == "ASSISTANCE_WORDING_UNAVAILABLE"
        assert result.interviewer_prompt_id is None
        assert provider.calls == 2
        async with sessions() as session:
            prompt = await session.scalar(
                select(InterviewerPrompt).where(
                    InterviewerPrompt.target_event_id == result.request_event_id
                )
            )
            assert prompt is not None and prompt.status == "REJECTED"
            budget = await assistance_budget_snapshot(session, session_id)
            assert budget is not None
            assert budget.outstanding_assistance_interventions == 0
    finally:
        await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_active_checkpoint_is_one_shot_and_reused_post_interview(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    concept_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            concept = Concept(
                canonical_key=f"stage6a_active_{development.interview_session.id.hex}",
                display_name="Active target",
                category="algorithm",
                status="ACTIVE",
                description="Active Evidence target",
            )
            session.add(concept)
            await session.flush()
            concept_id = concept.id
            session.add(
                ProblemConcept(
                    problem_version_id=development.problem_version.id,
                    concept_id=concept.id,
                    relevance="PRIMARY",
                    expected_importance="HIGH",
                    role="CORE",
                )
            )
            await RealtimeControlService(session).persist_candidate_transcript(
                session_id=development.interview_session.id,
                message=CandidateTranscriptFinalizedMessage(
                    type="candidate_transcript_finalized",
                    client_event_id="active-transcript",
                    client_instance_id="stage6a-test",
                    client_sequence=1,
                    provider_item_id="candidate-active-1",
                    transcript="I think resetting the left pointer is always safe.",
                ),
            )
            session_id = development.interview_session.id
            concept_key = concept.canonical_key

        provider = FakeAssessmentProvider(
            [
                {
                "findings": [
                    {
                        "assessment_dimension": "CORRECTNESS",
                        "polarity": "NEGATIVE",
                        "confidence": 0.95,
                        "technical_rationale": "Resetting the boundary violates monotonicity.",
                        "evidence_finding": "The candidate asserted an invalid reset.",
                        "proposed_strength": "MODERATE",
                        "source_aliases": ["source_1"],
                        "concept_keys": [concept_key],
                        "skill_dimension_keys": ["correctness"],
                        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                        "breakpoint_subtype": "left_pointer_monotonicity",
                        "breakpoint_effect": "WEAKNESS",
                        "breakpoint_severity": "HIGH",
                    }
                ]
                },
                {
                    "contract_version": "coach-assistance-output.v1",
                    "prompt_text": "Which left-pointer invariant is least certain?",
                },
            ]
        )
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions, ai_gateway=gateway
        )
        active = await coordinator.evaluate_active_checkpoint(session_id)
        assert active.completed_units == 1
        assert provider.calls == 1
        assert provider.requests[0].purpose == CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE
        assistance = await CoachAssistanceWorkflow(
            sessionmaker=sessions,
            evidence_coordinator=coordinator,
            wording_service=CoachAssistanceWordingService(gateway),
        ).request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="active-gap-assistance-request",
            )
        )
        assert assistance.status == "AUTHORIZED"
        assert assistance.hint_level == "METACOGNITIVE"
        assert assistance.target_concept_id == concept_id
        assert assistance.target_skill_dimension_id is not None
        assert provider.calls == 2
        assert provider.requests[1].purpose == COACH_ASSISTANCE_PURPOSE
        assert provider.requests[1].capability == "STANDARD_REASONING"
        assert provider.requests[1].policy.policy_key == "coach_assistance"
        assert provider.requests[1].policy.version == "v1"
        assert provider.requests[1].output_json_schema["properties"][
            "contract_version"
        ]["const"] == "coach-assistance-output.v1"
        wording_payload = json.loads(provider.requests[1].input_content)
        assert wording_payload["trusted_context"]["software_authorization"] == {
            "assistance_type": "METACOGNITIVE",
            "selected_hint_level": "METACOGNITIVE",
        }
        assert wording_payload["trusted_context"]["diagnostic_target"][
            "concept_key"
        ] == concept_key
        assert wording_payload["untrusted_candidate_context"]["authority"] == "NONE"
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            interview.status = "COMPLETED"
            interview.current_stage = "COMPLETED"
            interview.completed_at = datetime.now(UTC)
        post = await coordinator.evaluate(session_id)
        assert post.skipped_units == 1
        assert post.units[0].error_category == "ALREADY_EVALUATED"
        assert provider.calls == 2
        async with sessions() as session:
            evidence = await session.scalar(
                select(Evidence).where(Evidence.interview_session_id == session_id)
            )
            invocations = list(
                await session.scalars(
                    select(AIInvocation).where(
                        AIInvocation.interview_session_id == session_id
                    )
                )
            )
            assert evidence is not None and evidence.independence_level == "INDEPENDENT"
            assert {item.purpose for item in invocations} == {
                CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE,
                COACH_ASSISTANCE_PURPOSE,
            }
            budget = await session.scalar(
                select(SessionBudget).where(SessionBudget.session_id == session_id)
            )
            assert budget is not None
            assert budget.deep_reasoning_used == 2
            assert budget.reserved_post_interview_deep_reasoning_calls == 16
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        if concept_id:
            async with sessions() as session, session.begin():
                await session.execute(delete(Concept).where(Concept.id == concept_id))
        await engine.dispose()


async def test_active_checkpoint_evaluates_latest_stable_direct_code(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    concept_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            concept = Concept(
                canonical_key=f"stage6a_direct_active_{development.interview_session.id.hex}",
                display_name="Active direct code",
                category="algorithm",
                status="ACTIVE",
                description="Direct code checkpoint target",
            )
            session.add(concept)
            await session.flush()
            concept_id = concept.id
            session.add(
                ProblemConcept(
                    problem_version_id=development.problem_version.id,
                    concept_id=concept.id,
                    relevance="PRIMARY",
                    expected_importance="HIGH",
                    role="CORE",
                )
            )
            service = RealtimeControlService(session)
            await service.persist_candidate_code_snapshot(
                session_id=development.interview_session.id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="active-direct-initial",
                    client_instance_id="stage6a-test",
                    client_sequence=1,
                    source_code="int answer = 0;",
                    language="cpp",
                    trigger="INITIAL_EDITOR_STATE",
                ),
            )
            await service.persist_candidate_code_snapshot(
                session_id=development.interview_session.id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="active-direct-edit",
                    client_instance_id="stage6a-test",
                    client_sequence=2,
                    source_code="int answer = 1;",
                    language="cpp",
                    trigger="EDIT_BURST",
                ),
            )
            session_id = development.interview_session.id
            concept_key = concept.canonical_key
        provider = FakeAssessmentProvider(
            {
                "findings": [
                    {
                        "assessment_dimension": "CORRECTNESS",
                        "polarity": "POSITIVE",
                        "confidence": 0.9,
                        "technical_rationale": "The committed edit establishes the fact.",
                        "evidence_finding": "The latest stable code supports the target.",
                        "proposed_strength": "MODERATE",
                        "source_aliases": ["source_1"],
                        "concept_keys": [concept_key],
                        "skill_dimension_keys": ["correctness"],
                        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                        "breakpoint_subtype": None,
                        "breakpoint_effect": "NONE",
                        "breakpoint_severity": None,
                    }
                ]
            }
        )
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        ).evaluate_active_checkpoint(session_id)
        assert result.completed_units == 1
        assert result.units[0].unit_kind == "DIRECT_CODE"
        assert provider.calls == 1
        assert provider.requests[0].purpose == CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        if concept_id:
            async with sessions() as session, session.begin():
                await session.execute(delete(Concept).where(Concept.id == concept_id))
        await engine.dispose()


async def test_initial_editor_baseline_is_context_in_active_and_completed_evaluation(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            user_id = development.user.id
            persisted = await RealtimeControlService(session).persist_candidate_code_snapshot(
                session_id=development.interview_session.id,
                message=CandidateCodeSnapshotMessage(
                    type="candidate_code_snapshot",
                    client_event_id="baseline-only-initial",
                    client_instance_id="stage6a-baseline-test",
                    client_sequence=1,
                    source_code="return {};",
                    language="cpp",
                    trigger="INITIAL_EDITOR_STATE",
                ),
            )
            session_id = development.interview_session.id
            snapshot_id = persisted.snapshot_id

        provider = FakeAssessmentProvider(
            {
                "findings": [
                    {
                        "assessment_dimension": "CORRECTNESS",
                        "polarity": "NEGATIVE",
                        "confidence": 0.99,
                        "technical_rationale": "Starter code is incomplete.",
                        "evidence_finding": "The starter body returns an empty result.",
                        "proposed_strength": "STRONG",
                        "source_aliases": ["source_1"],
                        "concept_keys": [],
                        "skill_dimension_keys": ["correctness"],
                        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                        "breakpoint_subtype": None,
                        "breakpoint_effect": "WEAKNESS",
                        "breakpoint_severity": "HIGH",
                    }
                ]
            }
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        )
        active = await coordinator.evaluate_active_checkpoint(session_id)
        assert active.units == ()
        assert provider.calls == 0

        async with sessions() as session, session.begin():
            snapshot = await session.get(CodeSnapshot, snapshot_id)
            assert snapshot is not None
            assert snapshot.source_code == "return {};"
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            await InterviewCompletionService(session).complete(
                session_id=session_id,
                reason="USER_ENDED",
                expected_state_version=interview.state_version,
                idempotency_key="baseline-only-complete",
            )

        completed = await coordinator.evaluate(session_id)
        assert completed.units == ()
        assert provider.calls == 0
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Assessment)
                    .where(Assessment.interview_session_id == session_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                )
                == 0
            )
            assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Breakpoint)
                        .where(Breakpoint.user_id == user_id)
                    )
                == 0
            )
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_pre_assistance_evidence_and_breakpoint_survive_taught_success(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    concept_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            concept = Concept(
                canonical_key=f"stage6a_taught_{development.interview_session.id.hex}",
                display_name="Teaching boundary",
                category="algorithm",
                status="ACTIVE",
                description="Teaching integrity target",
            )
            session.add(concept)
            await session.flush()
            concept_id = concept.id
            session.add(
                ProblemConcept(
                    problem_version_id=development.problem_version.id,
                    concept_id=concept.id,
                    relevance="PRIMARY",
                    expected_importance="HIGH",
                    role="CORE",
                )
            )
            await RealtimeControlService(session).persist_candidate_transcript(
                session_id=development.interview_session.id,
                message=CandidateTranscriptFinalizedMessage(
                    type="candidate_transcript_finalized",
                    client_event_id="taught-negative-transcript",
                    client_instance_id="stage6a-test",
                    client_sequence=1,
                    provider_item_id="candidate-taught-negative",
                    transcript="I reset the boundary because it never affects correctness.",
                ),
            )
            session_id = development.interview_session.id
            concept_key = concept.canonical_key

        negative_provider = FakeAssessmentProvider(
            {
                "findings": [
                    {
                        "assessment_dimension": "CORRECTNESS",
                        "polarity": "NEGATIVE",
                        "confidence": 0.95,
                        "technical_rationale": "The asserted reset violates the invariant.",
                        "evidence_finding": "The independent attempt contains a stable gap.",
                        "proposed_strength": "STRONG",
                        "source_aliases": ["source_1"],
                        "concept_keys": [concept_key],
                        "skill_dimension_keys": ["correctness"],
                        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                        "breakpoint_subtype": None,
                        "breakpoint_effect": "WEAKNESS",
                        "breakpoint_severity": "HIGH",
                    }
                ]
            }
        )
        negative_result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=negative_provider,
            ),
        ).evaluate_active_checkpoint(session_id)
        assert negative_result.completed_units == 1
        assert len(negative_result.units[0].breakpoint_ids) == 1

        async with sessions() as session, session.begin():
            skill_id = await session.scalar(
                select(SkillDimension.id).where(SkillDimension.canonical_key == "correctness")
            )
            assert skill_id is not None
            request_event = (
                await InterviewRuntime(session).accept_event(
                    AcceptEventCommand(
                        session_id=session_id,
                        event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                        source="SYSTEM",
                        occurred_at=datetime.now(UTC),
                        idempotency_key="taught-assistance-request",
                    )
                )
            ).event
            prompt = await InterviewInteractionRepository(session).add_prompt(
                interview_session_id=session_id,
                origin="SYSTEM",
                kind="INSTRUCTION",
                intent="Keep the left boundary monotonic and update state before advancing it.",
                status="AUTHORIZED",
                assistance_type="DIRECT_TEACHING",
                hint_level="DIRECT_TEACHING",
                assistance_trigger="CANDIDATE_REQUEST",
                target_event_id=request_event.id,
                target_concept_id=concept_id,
                target_skill_dimension_id=skill_id,
                source_event_watermark=request_event.server_sequence,
                invites_guided_retry=True,
                authorized_at=datetime.now(UTC),
            )
            service = RealtimeControlService(session)
            started = await service.start_delivery(
                session_id=session_id,
                message=CounterQDeliveryStartedMessage(
                    type="counterq_delivery_started",
                    client_event_id="taught-delivery-start",
                    client_instance_id="stage6a-test",
                    client_sequence=2,
                    interviewer_prompt_id=prompt.id,
                    intended_text="Untrusted browser replacement.",
                    provider_response_id="taught-response",
                ),
            )
            await service.complete_delivery(
                session_id=session_id,
                message=CounterQDeliveryCompletedMessage(
                    type="counterq_delivery_completed",
                    client_event_id="taught-delivery-complete",
                    client_instance_id="stage6a-test",
                    client_sequence=3,
                    interviewer_prompt_id=prompt.id,
                    prompt_delivery_id=started.delivery_id,
                    provider_response_id="taught-response",
                    transcript=prompt.intent,
                ),
            )
            budget = await assistance_budget_snapshot(session, session_id)
            assert budget is not None
            assert budget.assistance_interventions_used == 1
            assert budget.direct_teaching_interventions_used == 1
            assert budget.guided_retries_used == 1
            assert development.budget.probes_used == 0
            await service.persist_candidate_transcript(
                session_id=session_id,
                message=CandidateTranscriptFinalizedMessage(
                    type="candidate_transcript_finalized",
                    client_event_id="taught-positive-transcript",
                    client_instance_id="stage6a-test",
                    client_sequence=4,
                    provider_item_id="candidate-taught-positive",
                    transcript="I now keep the boundary monotonic and update before advancing.",
                ),
            )

        positive_provider = FakeAssessmentProvider(
            {
                "findings": [
                    {
                        "assessment_dimension": "CORRECTNESS",
                        "polarity": "POSITIVE",
                        "confidence": 0.95,
                        "technical_rationale": "The candidate repeats the taught correction.",
                        "evidence_finding": "The immediate retry states the corrected invariant.",
                        "proposed_strength": "MODERATE",
                        "source_aliases": ["source_1"],
                        "concept_keys": [concept_key],
                        "skill_dimension_keys": ["correctness"],
                        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
                        "breakpoint_subtype": None,
                        "breakpoint_effect": "RESOLUTION_SUPPORT",
                        "breakpoint_severity": None,
                    }
                ]
            }
        )
        positive_result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=positive_provider,
            ),
        ).evaluate_active_checkpoint(session_id)
        assert positive_result.completed_units == 1
        assert positive_result.units[0].breakpoint_ids == ()
        async with sessions() as session:
            evidence = list(
                await session.scalars(
                    select(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                    .order_by(Evidence.created_at, Evidence.id)
                )
            )
            breakpoint = await session.scalar(
                select(Breakpoint).where(Breakpoint.first_detected_session_id == session_id)
            )
            link_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(BreakpointEvidence)
                    .where(BreakpointEvidence.breakpoint_id == breakpoint.id)
                )
                if breakpoint is not None
                else 0
            )
            assert [(item.polarity, item.independence_level) for item in evidence] == [
                ("NEGATIVE", "INDEPENDENT"),
                ("POSITIVE", "DIRECTLY_TAUGHT"),
            ]
            assert breakpoint is not None and breakpoint.status == "OPEN"
            assert link_count == 1
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        if concept_id:
            async with sessions() as session, session.begin():
                await session.execute(delete(Concept).where(Concept.id == concept_id))
        await engine.dispose()


async def test_active_checkpoint_invalid_schema_is_not_retried(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup: tuple[UUID, UUID, UUID] | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, mode="COACH", initial_stage="IMPLEMENTATION"
            )
            cleanup = (
                development.user.id,
                development.configuration.id,
                development.problem.id,
            )
            await RealtimeControlService(session).persist_candidate_transcript(
                session_id=development.interview_session.id,
                message=CandidateTranscriptFinalizedMessage(
                    type="candidate_transcript_finalized",
                    client_event_id="invalid-transcript",
                    client_instance_id="stage6a-test",
                    client_sequence=1,
                    provider_item_id="candidate-invalid-1",
                    transcript="I tried a loop but I am not sure about the boundary.",
                ),
            )
            session_id = development.interview_session.id
        provider = FakeAssessmentProvider({"not_findings": []})
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        ).evaluate_active_checkpoint(session_id)
        assert result.failed_units == 1
        assert result.units[0].error_category == "STRUCTURED_OUTPUT_INVALID"
        assert provider.calls == 1
        async with sessions() as session:
            evidence_count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id == session_id)
            )
            assert evidence_count == 0
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_active_checkpoint_excludes_incomplete_candidate_response(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, mode="COACH")
    event = (
        await InterviewRuntime(db_session).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="TRANSCRIPT_FINALIZED",
                source="CANDIDATE_VOICE",
                occurred_at=datetime.now(UTC),
                idempotency_key="stage6a-incomplete-response-event",
            )
        )
    ).event
    repository = InterviewInteractionRepository(db_session)
    response = await repository.add_response(
        interview_session_id=development.interview_session.id,
        started_at=datetime.now(UTC),
        ended_at=None,
        completion_reason="COMPLETE",
    )
    await repository.add_response_source(
        interview_session_id=development.interview_session.id,
        candidate_response_id=response.id,
        interview_event_id=event.id,
        source_role="PRIMARY",
        sequence=1,
    )
    units = await AssessmentInputBuilder(db_session).build_active_checkpoint(
        development.interview_session.id
    )
    assert units == []


async def _cleanup(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
    configuration_id: UUID,
    problem_id: UUID,
) -> None:
    async with sessions() as session, session.begin():
        await session.execute(delete(User).where(User.id == user_id))
        await session.execute(
            delete(InterviewConfiguration).where(InterviewConfiguration.id == configuration_id)
        )
        await session.execute(delete(Problem).where(Problem.id == problem_id))

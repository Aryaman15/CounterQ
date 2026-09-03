from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

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
from app.evidence.models import Breakpoint, BreakpointEvidence, Evidence, SkillDimension
from app.evidence.units import (
    AssessmentInputBuilder,
    AssessmentSourceFact,
    AssessmentUnit,
    AssessmentUnitKind,
)
from app.interviews.assistance import AssistanceRequestCommand, CoachAssistanceWorkflow
from app.interviews.budget_policy import assistance_budget_snapshot
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.mode_policy import ModePolicy
from app.interviews.models import (
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
)
from app.interviews.prompt_authorization import PromptAuthorizationService
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
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

    def __init__(self, output_data: dict[str, Any]) -> None:
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
        return ProviderReasoningResult(
            output_data=self.output_data,
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


async def test_coach_request_requires_attempt_then_authorizes_minimum_targeted_help() -> None:
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
        authorized = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-after-attempt",
            )
        )
        assert authorized.status == "AUTHORIZED"
        assert authorized.prompt_kind == "INSTRUCTION"
        assert authorized.assistance_type == "METACOGNITIVE"
        assert authorized.hint_level == "METACOGNITIVE"
        assert authorized.request_event_watermark > 0
        assert authorized.budget.outstanding_assistance_interventions == 1

        retry = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-after-attempt",
            )
        )
        assert retry.interviewer_prompt_id == authorized.interviewer_prompt_id
        assert retry.reason == "IDEMPOTENT_ASSISTANCE_REQUEST"
        concurrent = await workflow.request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="coach-concurrent-request",
            )
        )
        assert concurrent.interviewer_prompt_id == authorized.interviewer_prompt_id
        assert concurrent.reason == "OUTSTANDING_ASSISTANCE_ALREADY_AUTHORIZED"
    finally:
        if cleanup:
            await _cleanup(sessions, *cleanup)
        await engine.dispose()


async def test_candidate_progress_suppresses_authorized_assistance_before_delivery() -> None:
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
            await InterviewRuntime(session).accept_event(
                AcceptEventCommand(
                    session_id=session_id,
                    event_type="MEANINGFUL_CODE_CHANGE",
                    source="NATIVE_EDITOR",
                    occurred_at=datetime.now(UTC),
                    idempotency_key="stale-attempt",
                )
            )
        result = await CoachAssistanceWorkflow(sessionmaker=sessions).request(
            AssistanceRequestCommand(
                interview_session_id=session_id,
                idempotency_key="stale-hint",
            )
        )
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
        assert active.completed_units == 1
        assert provider.calls == 1
        assert provider.requests[0].purpose == CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE
        assistance = await CoachAssistanceWorkflow(
            sessionmaker=sessions, evidence_coordinator=coordinator
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
        assert provider.calls == 1
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            interview.status = "COMPLETED"
            interview.current_stage = "COMPLETED"
            interview.completed_at = datetime.now(UTC)
        post = await coordinator.evaluate(session_id)
        assert post.skipped_units == 1
        assert post.units[0].error_category == "ALREADY_EVALUATED"
        assert provider.calls == 1
        async with sessions() as session:
            evidence = await session.scalar(
                select(Evidence).where(Evidence.interview_session_id == session_id)
            )
            invocation = await session.scalar(
                select(AIInvocation).where(AIInvocation.interview_session_id == session_id)
            )
            assert evidence is not None and evidence.independence_level == "INDEPENDENT"
            assert invocation is not None
            assert invocation.purpose == CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE
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

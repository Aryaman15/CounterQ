from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_ai_gateway import FakeReasoningProvider
from test_stage5a_canonical_evaluation import evidence_fixture, validate_evidence

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningProviderError,
    ReasoningRequest,
)
from app.auth.models import User
from app.config.settings import create_settings, get_settings
from app.db.session import build_engine
from app.evidence.assessment_schema import AssessmentAnalysisResult, AssessmentFinding
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointService,
)
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.evidence.independence import IndependenceAttributionService
from app.evidence.models import (
    Assessment,
    AssessmentUnitEvaluation,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
)
from app.evidence.policy import (
    ASSESSMENT_EVALUATOR_INSTRUCTIONS,
    assessment_evaluator_policy_descriptor,
)
from app.evidence.snapshot import canonical_evaluation_snapshot
from app.evidence.units import (
    AssessmentInputBuilder,
    AssessmentUnitKind,
    is_successful_recovery_unit,
)
from app.evidence.validation import EvidenceValidationService
from app.execution.repository import ExecutionRepository
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    CandidateResponse,
    InterviewerPrompt,
    InterviewSession,
    SessionBudget,
)
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
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
    concept_key: str | None,
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
                "concept_keys": [concept_key] if concept_key is not None else [],
                "skill_dimension_keys": ["correctness"] if skill_keys is None else skill_keys,
                "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY" if weakness else "NONE",
                "breakpoint_subtype": None,
                "breakpoint_effect": "WEAKNESS" if weakness else "NONE",
                "breakpoint_severity": "HIGH" if weakness else None,
            }
        ]
    }


class _SelectiveAssessmentProvider(FakeReasoningProvider):
    def __init__(
        self,
        *,
        selector: Callable[[dict[str, Any]], bool],
        selected_output: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        super().__init__(output_data={"findings": []})
        self._selector = selector
        self._selected_output = selected_output

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        payload = cast(dict[str, Any], json.loads(request.input_content))
        self.output_data = (
            self._selected_output(payload) if self._selector(payload) else {"findings": []}
        )
        return await super().reason_structured(
            request,
            model=model,
            reasoning_effort=reasoning_effort,
        )


class _SequencedAssessmentProvider(FakeReasoningProvider):
    def __init__(
        self,
        outputs: list[dict[str, Any]],
        *,
        estimated_cost: Decimal | None = Decimal("0.000520"),
    ) -> None:
        if not outputs:
            raise ValueError("Sequenced provider requires at least one output")
        super().__init__(output_data=outputs[0], estimated_cost=estimated_cost)
        self._outputs = outputs

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        self.output_data = self._outputs[min(self.calls, len(self._outputs) - 1)]
        return await super().reason_structured(
            request,
            model=model,
            reasoning_effort=reasoning_effort,
        )


def _assert_completed_provider_usage(invocation: AIInvocation) -> None:
    assert invocation.provider_model_version == "gpt-5.6-terra-2026-08-24"
    assert invocation.provider_request_id == "provider-request-1"
    assert invocation.latency_ms == 42
    assert invocation.input_tokens == 100
    assert invocation.cached_input_tokens == 20
    assert invocation.output_tokens == 30
    assert invocation.retry_count == 0
    assert invocation.estimated_cost == Decimal("0.000520")
    assert invocation.currency == "USD"


def _unit_kind_is(kind: str) -> Callable[[dict[str, Any]], bool]:
    return lambda payload: payload["assessment_unit"]["kind"] == kind


def _payload_is_successful_recovery(payload: dict[str, Any]) -> bool:
    unit = payload["assessment_unit"]
    execution = unit.get("execution", {})
    previous = execution.get("previous_failed_execution")
    return (
        unit["kind"] == "EXECUTION_DEBUGGING"
        and execution.get("status") == "SUCCEEDED"
        and isinstance(previous, dict)
        and previous.get("status") != "SUCCEEDED"
    )


def _weakness_output_for_payload(
    payload: dict[str, Any], *, concept_key: str, polarity: str = "NEGATIVE"
) -> dict[str, Any]:
    source_aliases = [
        source["alias"] for source in payload["assessment_unit"]["source_allowlist"]
    ]
    return _analysis_output(
        concept_key=concept_key,
        polarity=polarity,
        source_aliases=source_aliases,
        weakness=True,
    )


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
) -> UUID:
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
    completed = await service.complete_delivery(
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
    assert completed.event_id is not None
    return completed.event_id


async def _candidate_fact(
    session: AsyncSession,
    interview_id: UUID,
    *,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, object] | None = None,
    code_snapshot_id: UUID | None = None,
) -> InterviewEvent:
    accepted = await InterviewRuntime(session).accept_event(
        AcceptEventCommand(
            session_id=interview_id,
            event_type=event_type,
            source="NATIVE_RUNNER",
            occurred_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
            payload=payload or {},
            code_snapshot_id=code_snapshot_id,
        )
    )
    return accepted.event


async def _completed_execution(
    session: AsyncSession,
    interview: InterviewSession,
    *,
    code_snapshot_id: UUID,
    status: str,
    idempotency_key: str,
) -> InterviewEvent:
    event = await _candidate_fact(
        session,
        interview.id,
        event_type="RUN_CLICKED",
        idempotency_key=f"{idempotency_key}:event",
        payload={"trigger": "CANDIDATE_RUN"},
        code_snapshot_id=code_snapshot_id,
    )
    run = await ExecutionRepository(session).add_run(
        session_id=interview.id,
        run_event_id=event.id,
        code_snapshot_id=code_snapshot_id,
        problem_version_id=interview.problem_version_id,
        language="cpp",
        started_at=datetime.now(UTC),
        execution_provider="stage5-test-double",
        idempotency_key=idempotency_key,
    )
    run.status = status
    run.completed_at = datetime.now(UTC)
    await session.flush()
    return event


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
    *,
    with_probe: bool = False,
    candidate_transcript: str = "This is one bounded finalized candidate demonstration.",
) -> tuple[UUID, UUID, str, UUID]:
    async with sessions() as session, session.begin():
        dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        dev.budget.max_deep_reasoning_calls = 20
        concept_key = f"stage5_admission_{str(dev.interview_session.id).replace('-', '_')}"
        concept = await _attach_problem_concept(session, dev.interview_session, key=concept_key)
        service = RealtimeControlService(session)
        sequence = 1
        if with_probe:
            prompt = await _prompt(
                session,
                dev.interview_session.id,
                kind="PROBE",
                text="Test the candidate's unresolved technical boundary.",
            )
            await _deliver(
                service,
                dev.interview_session.id,
                prompt,
                sequence=sequence,
                actual="Why does that invariant hold?",
            )
            sequence = 3
        await _candidate_turn(
            service,
            dev.interview_session.id,
            sequence=sequence,
            provider_item_id=f"admission-{dev.interview_session.id}",
            transcript=candidate_transcript,
        )
        await InterviewCompletionService(session).complete(
            session_id=dev.interview_session.id,
            reason="USER_ENDED",
            expected_state_version=dev.interview_session.state_version,
            idempotency_key=f"stage5-admission-complete:{dev.interview_session.id}",
        )
        return dev.interview_session.id, dev.user.id, concept_key, concept.id


async def _create_committed_recovery_session(
    sessions: async_sessionmaker[AsyncSession],
    *,
    with_probe: bool,
    user_id: UUID | None = None,
    concept_id: UUID | None = None,
    concept_key: str | None = None,
) -> tuple[UUID, UUID, UUID, str, UUID]:
    async with sessions() as session, session.begin():
        dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        dev.budget.max_deep_reasoning_calls = 20
        created_user_id = dev.user.id
        if user_id is not None:
            dev.interview_session.user_id = user_id
        if concept_id is None:
            resolved_key = (
                concept_key
                or f"stage5_recovery_{str(dev.interview_session.id).replace('-', '_')}"
            )
            concept = await _attach_problem_concept(
                session, dev.interview_session, key=resolved_key
            )
            concept_id = concept.id
        else:
            if concept_key is None:
                raise ValueError("Reused recovery Concept requires its canonical key")
            resolved_key = concept_key
            session.add(
                ProblemConcept(
                    problem_version_id=dev.problem_version.id,
                    concept_id=concept_id,
                    relevance="CORE",
                    expected_importance="HIGH",
                    role="PRIMARY",
                )
            )
            await session.flush()

        service = RealtimeControlService(session)
        previous = await service.persist_candidate_code_snapshot(
            session_id=dev.interview_session.id,
            message=CandidateCodeSnapshotMessage(
                **_client(1),
                type="candidate_code_snapshot",
                source_code="left = mid;",
                language="cpp",
                trigger="INITIAL_EDITOR_STATE",
            ),
        )
        await _completed_execution(
            session,
            dev.interview_session,
            code_snapshot_id=previous.snapshot_id,
            status="RUNTIME_ERROR",
            idempotency_key=f"recovery-failed:{dev.interview_session.id}",
        )
        delivery_event_id: UUID | None = None
        if with_probe:
            prompt = await _prompt(
                session,
                dev.interview_session.id,
                kind="PROBE",
                text="Test the failed boundary.",
            )
            delivery_event_id = await _deliver(
                service,
                dev.interview_session.id,
                prompt,
                sequence=2,
                actual="Which update guarantees progress?",
            )
        current = await service.persist_candidate_code_snapshot(
            session_id=dev.interview_session.id,
            message=CandidateCodeSnapshotMessage(
                **_client(4 if with_probe else 2),
                type="candidate_code_snapshot",
                source_code="left = mid + 1;",
                language="cpp",
                trigger="EDIT_BURST",
            ),
        )
        if delivery_event_id is not None:
            current_event = await session.get(InterviewEvent, current.event_id)
            assert current_event is not None
            current_event.causation_id = delivery_event_id
            await session.flush()
        await _completed_execution(
            session,
            dev.interview_session,
            code_snapshot_id=current.snapshot_id,
            status="SUCCEEDED",
            idempotency_key=f"recovery-succeeded:{dev.interview_session.id}",
        )
        await InterviewCompletionService(session).complete(
            session_id=dev.interview_session.id,
            reason="USER_ENDED",
            expected_state_version=dev.interview_session.state_version,
            idempotency_key=f"recovery-complete:{dev.interview_session.id}",
        )
        return (
            dev.interview_session.id,
            dev.interview_session.user_id,
            created_user_id,
            resolved_key,
            concept_id,
        )


async def _create_committed_direct_code_session(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, str, UUID]:
    async with sessions() as session, session.begin():
        dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        dev.budget.max_deep_reasoning_calls = 20
        concept_key = f"stage5_direct_{str(dev.interview_session.id).replace('-', '_')}"
        concept = await _attach_problem_concept(
            session, dev.interview_session, key=concept_key
        )
        await RealtimeControlService(session).persist_candidate_code_snapshot(
            session_id=dev.interview_session.id,
            message=CandidateCodeSnapshotMessage(
                **_client(1),
                type="candidate_code_snapshot",
                source_code="while (left <= right) { left = mid; }",
                language="cpp",
                trigger="EDIT_BURST",
            ),
        )
        await InterviewCompletionService(session).complete(
            session_id=dev.interview_session.id,
            reason="USER_ENDED",
            expected_state_version=dev.interview_session.state_version,
            idempotency_key=f"direct-complete:{dev.interview_session.id}",
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
    assert interrupted_attribution.reason == "INTERRUPTED_PROBE_CAUSALITY_AMBIGUOUS"
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
    delivery_event_id = await _deliver(
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

    event.causation_id = delivery_event_id
    await db_session.flush()
    linked = await IndependenceAttributionService(db_session).for_direct_event(event)
    assert linked.level == "AFTER_PROBE"
    assert linked.reason == "EXPLICIT_PROBE_CAUSAL_LINK"


async def test_direct_event_after_interrupted_probe_is_unresolved(
    db_session: AsyncSession,
) -> None:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = RealtimeControlService(db_session)
    probe = await _prompt(
        db_session,
        dev.interview_session.id,
        kind="PROBE",
        text="UNDISCLOSED INTERRUPTED PROBE REMAINDER",
    )
    started = await service.start_delivery(
        session_id=dev.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            **_client(1),
            type="counterq_delivery_started",
            interviewer_prompt_id=probe.id,
            intended_text=probe.intent,
            provider_response_id="direct-interrupted",
        ),
    )
    await service.interrupt_delivery(
        session_id=dev.interview_session.id,
        message=CounterQDeliveryInterruptedMessage(
            **_client(2),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=probe.id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="direct-interrupted",
            confirmed_by="output_audio_buffer.cleared",
        ),
    )
    code = await service.persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **_client(3),
            type="candidate_code_snapshot",
            source_code="int answer = 1;",
            language="cpp",
            trigger="EDIT_BURST",
        ),
    )
    event = await db_session.get(InterviewEvent, code.event_id)
    assert event is not None

    attribution = await IndependenceAttributionService(db_session).for_direct_event(event)

    assert attribution.level is None
    assert attribution.reason == "INTERRUPTED_PROBE_CAUSALITY_AMBIGUOUS"


async def test_direct_code_after_probe_response_is_not_upgraded_to_independent(
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
        actual="Why is the boundary monotonic?",
    )
    await _candidate_turn(
        service,
        dev.interview_session.id,
        sequence=3,
        provider_item_id="probe-response-before-code",
        transcript="Moving backward would reintroduce invalid state.",
    )
    code = await service.persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **_client(4),
            type="candidate_code_snapshot",
            source_code="left = max(left, last_seen + 1);",
            language="cpp",
            trigger="EDIT_BURST",
        ),
    )
    event = await db_session.get(InterviewEvent, code.event_id)
    assert event is not None

    attribution = await IndependenceAttributionService(db_session).for_direct_event(event)

    assert attribution.level is None
    assert attribution.reason == "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS"


async def test_time_warning_does_not_become_response_prompt_or_erase_probe(
    db_session: AsyncSession,
) -> None:
    ambiguous = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    ambiguous_service = RealtimeControlService(db_session)
    probe = await _prompt(db_session, ambiguous.interview_session.id, kind="PROBE", text="Probe")
    await _deliver(
        ambiguous_service,
        ambiguous.interview_session.id,
        probe,
        sequence=1,
        actual="Which invariant justifies that update?",
    )
    warning = await _prompt(
        db_session,
        ambiguous.interview_session.id,
        kind="TIME_WARNING",
        text="Two minutes remain.",
    )
    await _deliver(
        ambiguous_service,
        ambiguous.interview_session.id,
        warning,
        sequence=3,
        actual="Two minutes remain.",
    )
    ambiguous_event_id = await _candidate_turn(
        ambiguous_service,
        ambiguous.interview_session.id,
        sequence=5,
        provider_item_id="after-probe-warning",
        transcript="I need to preserve the monotonic boundary.",
    )
    ambiguous_response = await db_session.scalar(
        select(CandidateResponse)
        .join(CandidateResponse.sources)
        .where(CandidateResponse.sources.any(interview_event_id=ambiguous_event_id))
    )
    assert ambiguous_response is not None
    assert ambiguous_response.interviewer_prompt_id is None
    ambiguous_attribution = await IndependenceAttributionService(db_session).for_response(
        ambiguous_response
    )
    assert ambiguous_attribution.level is None
    assert ambiguous_attribution.reason == "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS"

    warning_only = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    warning_service = RealtimeControlService(db_session)
    lone_warning = await _prompt(
        db_session,
        warning_only.interview_session.id,
        kind="TIME_WARNING",
        text="One minute remains.",
    )
    await _deliver(
        warning_service,
        warning_only.interview_session.id,
        lone_warning,
        sequence=1,
        actual="One minute remains.",
    )
    independent_event_id = await _candidate_turn(
        warning_service,
        warning_only.interview_session.id,
        sequence=3,
        provider_item_id="after-warning-only",
        transcript="I will finish the implementation.",
    )
    independent_response = await db_session.scalar(
        select(CandidateResponse)
        .join(CandidateResponse.sources)
        .where(CandidateResponse.sources.any(interview_event_id=independent_event_id))
    )
    assert independent_response is not None
    assert independent_response.interviewer_prompt_id is None
    assert (
        await IndependenceAttributionService(db_session).for_response(independent_response)
    ).level == "INDEPENDENT"


async def test_debugging_independence_uses_complete_failure_to_success_window(
    db_session: AsyncSession,
) -> None:
    probed = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    failure = await _candidate_fact(
        db_session,
        probed.interview_session.id,
        event_type="TEST_COMPLETED",
        idempotency_key="debug-window-failed",
        payload={"status": "FAILED"},
    )
    probe = await _prompt(db_session, probed.interview_session.id, kind="PROBE", text="Probe")
    await _deliver(
        RealtimeControlService(db_session),
        probed.interview_session.id,
        probe,
        sequence=1,
        actual="What caused that failing case?",
    )
    success = await _candidate_fact(
        db_session,
        probed.interview_session.id,
        event_type="TEST_COMPLETED",
        idempotency_key="debug-window-succeeded",
        payload={"status": "SUCCEEDED"},
    )
    probed_attribution = await IndependenceAttributionService(db_session).for_event_window(
        (failure, success)
    )
    assert probed_attribution.level is None
    assert probed_attribution.reason == "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS"

    independent = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    independent_failure = await _candidate_fact(
        db_session,
        independent.interview_session.id,
        event_type="TEST_COMPLETED",
        idempotency_key="independent-debug-failed",
        payload={"status": "FAILED"},
    )
    independent_success = await _candidate_fact(
        db_session,
        independent.interview_session.id,
        event_type="TEST_COMPLETED",
        idempotency_key="independent-debug-succeeded",
        payload={"status": "SUCCEEDED"},
    )
    independent_attribution = await IndependenceAttributionService(db_session).for_event_window(
        (independent_failure, independent_success)
    )
    assert independent_attribution.level == "INDEPENDENT"


@pytest.mark.parametrize("with_probe", [False, True])
async def test_assessment_builder_applies_debugging_window_independence(
    db_session: AsyncSession,
    with_probe: bool,
) -> None:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    interview = dev.interview_session
    service = RealtimeControlService(db_session)
    previous = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=CandidateCodeSnapshotMessage(
            **_client(1),
            type="candidate_code_snapshot",
            source_code="int answer = 0;",
            language="cpp",
            trigger="INITIAL_EDITOR_STATE",
        ),
    )
    await _completed_execution(
        db_session,
        interview,
        code_snapshot_id=previous.snapshot_id,
        status="COMPILE_ERROR",
        idempotency_key=f"builder-failed-{with_probe}",
    )
    if with_probe:
        probe = await _prompt(db_session, interview.id, kind="PROBE", text="Probe")
        await _deliver(
            service,
            interview.id,
            probe,
            sequence=2,
            actual="What caused the failed execution?",
        )
    current = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=CandidateCodeSnapshotMessage(
            **_client(4 if with_probe else 2),
            type="candidate_code_snapshot",
            source_code="int answer = 1;",
            language="cpp",
            trigger="EDIT_BURST",
        ),
    )
    await _completed_execution(
        db_session,
        interview,
        code_snapshot_id=current.snapshot_id,
        status="SUCCEEDED",
        idempotency_key=f"builder-succeeded-{with_probe}",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=interview.id,
        reason="USER_ENDED",
        expected_state_version=interview.state_version,
        idempotency_key=f"builder-debug-complete-{with_probe}",
    )

    units = await AssessmentInputBuilder(db_session).build_completed_simulation(interview.id)
    failed_execution = next(
        unit
        for unit in units
        if unit.kind == AssessmentUnitKind.EXECUTION_DEBUGGING
        and unit.source_code_snapshot_id == previous.snapshot_id
    )
    debugging = next(
        unit
        for unit in units
        if unit.kind == AssessmentUnitKind.EXECUTION_DEBUGGING
        and unit.source_code_snapshot_id == current.snapshot_id
    )
    assessment_unit = cast(dict[str, object], debugging.input_payload["assessment_unit"])
    execution = cast(dict[str, object], assessment_unit["execution"])
    previous_failed = cast(dict[str, object], execution["previous_failed_execution"])
    previous_code = cast(dict[str, object], previous_failed["code_snapshot"])
    current_code = cast(dict[str, object], execution["code_snapshot"])

    assert not any(
        unit.kind == AssessmentUnitKind.DIRECT_CODE
        and unit.source_code_snapshot_id == previous.snapshot_id
        for unit in units
    )
    assert previous_code["id"] == str(previous.snapshot_id)
    assert current_code["id"] == str(current.snapshot_id)
    assert any(
        cast(dict[str, object], event)["code_diff"] is not None
        for event in cast(list[object], execution["behavior_sequence"])
    )
    assert is_successful_recovery_unit(failed_execution) is False
    assert is_successful_recovery_unit(debugging) is True
    if with_probe:
        assert debugging.independence_level is None
        assert debugging.independence_reason == "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS"
    else:
        assert debugging.independence_level == "INDEPENDENT"


async def test_ordinary_independent_code_diff_remains_direct_code(
    db_session: AsyncSession,
) -> None:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = RealtimeControlService(db_session)
    baseline = await service.persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **_client(1),
            type="candidate_code_snapshot",
            source_code="int answer = 0;",
            language="cpp",
            trigger="INITIAL_EDITOR_STATE",
        ),
    )
    revision = await service.persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **_client(2),
            type="candidate_code_snapshot",
            source_code="int answer = 1;",
            language="cpp",
            trigger="EDIT_BURST",
        ),
    )
    await InterviewCompletionService(db_session).complete(
        session_id=dev.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=dev.interview_session.state_version,
        idempotency_key="ordinary-revision-complete",
    )

    units = await AssessmentInputBuilder(db_session).build_completed_simulation(
        dev.interview_session.id
    )
    direct_units = [item for item in units if item.kind == AssessmentUnitKind.DIRECT_CODE]
    assert len(direct_units) == 1
    unit = next(item for item in units if item.source_code_snapshot_id == revision.snapshot_id)
    assessment_unit = cast(dict[str, object], unit.input_payload["assessment_unit"])
    code = cast(dict[str, object], assessment_unit["code"])
    previous = cast(dict[str, object], code["previous"])

    assert unit.kind == AssessmentUnitKind.DIRECT_CODE
    assert previous["id"] == str(baseline.snapshot_id)
    assert is_successful_recovery_unit(unit) is False
    assert code["candidate_revision_observed"] is True
    assert code["correction_status"] == "NOT_DETERMINED_BY_SOFTWARE"
    assert "self_correction" not in unit.serialize().lower()
    rebuilt = await AssessmentInputBuilder(db_session).build_completed_simulation(
        dev.interview_session.id
    )
    rebuilt_unit = next(
        item for item in rebuilt if item.source_code_snapshot_id == revision.snapshot_id
    )
    assert rebuilt_unit.unit_key == unit.unit_key


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


async def test_rebuttal_links_only_the_exact_normalized_breakpoint_boundary(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    negative = await validate_evidence(db_session, fixture)
    service = BreakpointService(db_session)
    base_candidate = BreakpointCandidate(
        user_id=fixture.graph.user.id,
        interview_session_id=fixture.graph.interview_session.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        evidence_ids=(negative.id,),
        boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
        summary="One canonical target with distinct normalized boundaries.",
        severity="HIGH",
    )
    fallback = await service.create_or_reinforce(base_candidate)
    subtype = await service.create_or_reinforce(
        replace(base_candidate, known_subtype="worst_case_complexity")
    )
    assert fallback.breakpoint_id is not None
    assert subtype.breakpoint_id is not None
    assert fallback.breakpoint_id != subtype.breakpoint_id
    assert fallback.breakpoint_key != subtype.breakpoint_key

    contradiction = await validate_evidence(
        db_session,
        fixture,
        polarity="POSITIVE",
        finding="The candidate now distinguishes average and worst-case behavior.",
    )
    resolution_support = await validate_evidence(
        db_session,
        fixture,
        polarity="POSITIVE",
        finding="A separate current demonstration supports the fallback boundary.",
    )
    unmatched = await validate_evidence(
        db_session,
        fixture,
        polarity="POSITIVE",
        finding="This valid Evidence has no matching active normalized boundary.",
    )

    contradicted_id = await service.link_evidence_to_active_boundary(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        known_subtype="worst_case_complexity",
        evidence_id=contradiction.id,
        relationship="CONTRADICTED",
    )
    resolution_id = await service.link_evidence_to_active_boundary(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        known_subtype=None,
        evidence_id=resolution_support.id,
        relationship="RESOLUTION_SUPPORT",
    )
    unmatched_id = await service.link_evidence_to_active_boundary(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        skill_dimension_id=fixture.skill.id,
        assessment_dimension="CORRECTNESS",
        known_subtype="left_pointer_monotonicity",
        evidence_id=unmatched.id,
        relationship="CONTRADICTED",
    )

    assert contradicted_id == subtype.breakpoint_id
    assert resolution_id == fallback.breakpoint_id
    assert unmatched_id is None
    positive_links = list(
        await db_session.scalars(
            select(BreakpointEvidence).where(
                BreakpointEvidence.evidence_id.in_(
                    (contradiction.id, resolution_support.id, unmatched.id)
                )
            )
        )
    )
    assert {
        (link.breakpoint_id, link.evidence_id, link.relationship) for link in positive_links
    } == {
        (subtype.breakpoint_id, contradiction.id, "CONTRADICTED"),
        (fallback.breakpoint_id, resolution_support.id, "RESOLUTION_SUPPORT"),
    }
    assert await db_session.get(Evidence, unmatched.id) is not None


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
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == session_id)
            )
        assert statuses == ("REJECTED", "REJECTED", "REJECTED")
        assert evidence_count == 0
        assert breakpoint_count == 0
        assert invocation_count == 1
        assert provider.calls == 1
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_concept_only_skill_only_and_combined_findings_become_evidence(
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
        concept_only = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key=concept_key,
                dimension="CORRECTNESS",
                skill_keys=[],
            )["findings"],
        )[0]
        skill_only = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key=None,
                dimension="EXPLANATION_QUALITY",
                skill_keys=["explanation_clarity"],
            )["findings"],
        )[0]
        concept_and_skill = cast(
            list[dict[str, Any]],
            _analysis_output(
                concept_key=concept_key,
                dimension="DEPTH",
                skill_keys=["correctness"],
            )["findings"],
        )[0]
        provider = FakeReasoningProvider(
            output_data={"findings": [concept_only, skill_only, concept_and_skill]}
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        )

        result = await coordinator.evaluate(session_id)

        assert result.completed_units == 1
        assert sum(len(unit.evidence_ids) for unit in result.units) == 3
        async with sessions() as session:
            unit = (await AssessmentInputBuilder(session).build_completed_simulation(session_id))[0]
            invocation = await session.scalar(
                select(AIInvocation)
                .where(AIInvocation.interview_session_id == session_id)
                .order_by(AIInvocation.started_at.desc())
                .limit(1)
            )
        assert invocation is not None
        # Exercise the low-level admission defense without the coordinator's
        # unit-completion short circuit.
        async with sessions() as session, session.begin():
            await session.execute(
                delete(AssessmentUnitEvaluation).where(
                    AssessmentUnitEvaluation.interview_session_id == session_id
                )
            )
        no_target = AssessmentFinding.model_construct(
            assessment_dimension="TRANSFER",
            polarity="POSITIVE",
            confidence=0.9,
            technical_rationale="This bypasses model validation to exercise admission defense.",
            evidence_finding="No canonical target was supplied.",
            proposed_strength="MODERATE",
            source_aliases=["source_1"],
            concept_keys=[],
            skill_dimension_keys=[],
            boundary_kind="NONE",
            breakpoint_subtype=None,
            breakpoint_effect="NONE",
            breakpoint_severity=None,
        )
        rejected = await coordinator._persist_result(
            original_unit=unit,
            analysis=AssessmentAnalysisResult.model_construct(findings=[no_target]),
            invocation_id=invocation.id,
            evaluator_policy_version_id=invocation.ai_policy_version_id,
        )
        assert rejected.evidence_ids == ()
        async with sessions() as session:
            assessments = list(
                await session.scalars(
                    select(Assessment).where(Assessment.interview_session_id == session_id)
                )
            )
            evidence_rows = list(
                await session.scalars(
                    select(Evidence).where(Evidence.interview_session_id == session_id)
                )
            )
            link_counts = {
                evidence.evidence_type: (
                    int(
                        await session.scalar(
                            select(func.count())
                            .select_from(EvidenceConcept)
                            .where(EvidenceConcept.evidence_id == evidence.id)
                        )
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(func.count())
                            .select_from(EvidenceSkill)
                            .where(EvidenceSkill.evidence_id == evidence.id)
                        )
                        or 0
                    ),
                )
                for evidence in evidence_rows
            }
        assert {assessment.status for assessment in assessments} == {"VALIDATED", "REJECTED"}
        assert len(assessments) == 4
        assert len(evidence_rows) == 3
        assert link_counts == {
            "CORRECTNESS": (1, 0),
            "EXPLANATION_QUALITY": (0, 1),
            "DEPTH": (1, 1),
        }
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_independent_successful_recovery_preserves_evidence_without_breakpoint_on_retry(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        session_id, user_id, created_user_id, concept_key, concept_id = (
            await _create_committed_recovery_session(sessions, with_probe=False)
        )
        cleanup_user_ids.update((user_id, created_user_id))
        cleanup_concept_ids.add(concept_id)
        provider = _SelectiveAssessmentProvider(
            selector=_payload_is_successful_recovery,
            selected_output=lambda payload: _weakness_output_for_payload(
                payload, concept_key=concept_key, polarity="MIXED"
            ),
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        )

        first = await coordinator.evaluate(session_id)
        second = await coordinator.evaluate(session_id)

        recovery = next(unit for unit in first.units if unit.evidence_ids)
        retried_recovery = next(unit for unit in second.units if unit.evidence_ids)
        assert recovery.unit_kind == "EXECUTION_DEBUGGING"
        assert recovery.breakpoint_ids == ()
        assert retried_recovery.error_category == "ALREADY_EVALUATED"
        assert retried_recovery.breakpoint_ids == ()
        async with sessions() as session:
            evidence = await session.scalar(
                select(Evidence).where(Evidence.interview_session_id == session_id)
            )
            breakpoint_count = await session.scalar(
                select(func.count()).select_from(Breakpoint).where(Breakpoint.user_id == user_id)
            )
            link_count = await session.scalar(
                select(func.count())
                .select_from(BreakpointEvidence)
                .join(Evidence, Evidence.id == BreakpointEvidence.evidence_id)
                .where(Evidence.interview_session_id == session_id)
            )
        assert evidence is not None
        assert evidence.polarity == "MIXED"
        assert evidence.independence_level == "INDEPENDENT"
        assert breakpoint_count == 0
        assert link_count == 0
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_after_probe_successful_recovery_preserves_attribution_without_breakpoint(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        session_id, user_id, created_user_id, concept_key, concept_id = (
            await _create_committed_recovery_session(sessions, with_probe=True)
        )
        cleanup_user_ids.update((user_id, created_user_id))
        cleanup_concept_ids.add(concept_id)
        provider = _SelectiveAssessmentProvider(
            selector=_payload_is_successful_recovery,
            selected_output=lambda payload: _weakness_output_for_payload(
                payload, concept_key=concept_key, polarity="MIXED"
            ),
        )
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        ).evaluate(session_id)

        recovery = next(unit for unit in result.units if unit.evidence_ids)
        assert recovery.breakpoint_ids == ()
        async with sessions() as session:
            evidence = await session.scalar(
                select(Evidence).where(Evidence.interview_session_id == session_id)
            )
            breakpoint_count = await session.scalar(
                select(func.count()).select_from(Breakpoint).where(Breakpoint.user_id == user_id)
            )
        assert evidence is not None
        assert evidence.independence_level == "AFTER_PROBE"
        assert breakpoint_count == 0
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_unresolved_direct_bug_creates_breakpoint_recovery_does_not_reinforce_it(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        direct_session_id, user_id, concept_key, concept_id = (
            await _create_committed_direct_code_session(sessions)
        )
        cleanup_user_ids.add(user_id)
        cleanup_concept_ids.add(concept_id)
        direct_provider = _SelectiveAssessmentProvider(
            selector=_unit_kind_is("DIRECT_CODE"),
            selected_output=lambda payload: _weakness_output_for_payload(
                payload, concept_key=concept_key
            ),
        )
        direct_result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=direct_provider,
            ),
        ).evaluate(direct_session_id)
        assert len(direct_result.units[0].breakpoint_ids) == 1

        recovery_session_id, _, recovery_user_id, _, _ = (
            await _create_committed_recovery_session(
                sessions,
                with_probe=False,
                user_id=user_id,
                concept_id=concept_id,
                concept_key=concept_key,
            )
        )
        cleanup_user_ids.add(recovery_user_id)
        recovery_provider = _SelectiveAssessmentProvider(
            selector=_payload_is_successful_recovery,
            selected_output=lambda payload: _weakness_output_for_payload(
                payload, concept_key=concept_key, polarity="MIXED"
            ),
        )
        recovery_result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=recovery_provider,
            ),
        ).evaluate(recovery_session_id)

        assert all(not unit.breakpoint_ids for unit in recovery_result.units)
        async with sessions() as session:
            breakpoints = list(
                await session.scalars(
                    select(Breakpoint).where(Breakpoint.user_id == user_id)
                )
            )
            relationships = list(
                await session.scalars(
                    select(BreakpointEvidence.relationship).where(
                        BreakpointEvidence.breakpoint_id == breakpoints[0].id
                    )
                )
            )
            recovery_evidence = await session.scalar(
                select(Evidence).where(
                    Evidence.interview_session_id == recovery_session_id
                )
            )
        assert len(breakpoints) == 1
        assert breakpoints[0].status == "OPEN"
        assert relationships == ["CREATED"]
        assert recovery_evidence is not None
        assert recovery_evidence.polarity == "MIXED"
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_unresolved_prompted_misconception_after_probe_still_creates_breakpoint(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        session_id, user_id, concept_key, concept_id = (
            await _create_committed_response_session(
                sessions,
                with_probe=True,
                candidate_transcript=(
                    "The pointer can stay at mid because the loop will eventually terminate."
                ),
            )
        )
        cleanup_user_ids.add(user_id)
        cleanup_concept_ids.add(concept_id)
        provider = _SelectiveAssessmentProvider(
            selector=_unit_kind_is("PROMPTED_RESPONSE"),
            selected_output=lambda payload: _weakness_output_for_payload(
                payload, concept_key=concept_key
            ),
        )
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        ).evaluate(session_id)

        assert len(result.units) == 1
        assert len(result.units[0].breakpoint_ids) == 1
        async with sessions() as session:
            evidence = await session.scalar(
                select(Evidence).where(Evidence.interview_session_id == session_id)
            )
            breakpoint = await session.scalar(
                select(Breakpoint).where(Breakpoint.user_id == user_id)
            )
        assert evidence is not None
        assert evidence.independence_level == "AFTER_PROBE"
        assert breakpoint is not None
        assert breakpoint.status == "OPEN"
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_structured_output_retry_invalid_then_valid_persists_only_second_attempt(
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
        provider = _SequencedAssessmentProvider(
            [
                {"findings": [{"malformed": True}]},
                _analysis_output(concept_key=concept_key, weakness=True),
            ]
        )
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        )

        result = await coordinator.evaluate(session_id)

        assert result.completed_units == 1
        assert result.failed_units == 0
        assert len(result.units[0].assessment_ids) == 1
        assert len(result.units[0].evidence_ids) == 1
        assert len(result.units[0].breakpoint_ids) == 1
        assert provider.calls == 2
        assert [request.metadata["attempt"] for request in provider.requests] == [1, 2]
        assert all(
            request.metadata["assessment_unit_key"] == result.units[0].unit_key
            for request in provider.requests
        )
        assert [request.correlation_id for request in provider.requests] == [
            f"{result.units[0].unit_key}:attempt:1",
            f"{result.units[0].unit_key}:attempt:2",
        ]
        assert provider.requests[0].input_content == provider.requests[1].input_content
        assert provider.requests[0].policy == provider.requests[1].policy

        async with sessions() as session:
            invocations = list(
                await session.scalars(
                    select(AIInvocation)
                    .where(AIInvocation.interview_session_id == session_id)
                    .order_by(AIInvocation.started_at, AIInvocation.id)
                )
            )
            assessments = list(
                await session.scalars(
                    select(Assessment).where(Assessment.interview_session_id == session_id)
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
            budget = await session.get(SessionBudget, session_id)
        assert [(item.status, item.error_class) for item in invocations] == [
            ("FAILED", "STRUCTURED_OUTPUT_INVALID"),
            ("SUCCEEDED", None),
        ]
        for invocation in invocations:
            _assert_completed_provider_usage(invocation)
        assert len(assessments) == 1
        assert assessments[0].ai_invocation_id == invocations[1].id
        assert evidence_count == 1
        assert breakpoint_count == 1
        assert budget is not None
        assert budget.estimated_cost == Decimal("0.0010")

        repeated = await coordinator.evaluate(session_id)
        assert repeated.skipped_units == 1
        assert repeated.units[0].error_category == "ALREADY_EVALUATED"
        assert provider.calls == 2
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_schema_invalid_cost_can_block_stage5_retry_before_second_provider_call(
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
        async with sessions() as session, session.begin():
            budget = await session.get(SessionBudget, session_id)
            assert budget is not None
            budget.hard_monetary_budget = Decimal("0.0050")

        provider = _SequencedAssessmentProvider(
            [
                {"findings": [{"malformed": True}]},
                _analysis_output(concept_key=concept_key),
            ],
            estimated_cost=Decimal("0.006000"),
        )
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        ).evaluate(session_id)

        assert result.failed_units == 1
        assert result.units[0].error_category == "BUDGET_EXHAUSTED"
        assert provider.calls == 1
        async with sessions() as session:
            invocations = list(
                await session.scalars(
                    select(AIInvocation).where(AIInvocation.interview_session_id == session_id)
                )
            )
            budget = await session.get(SessionBudget, session_id)
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
        assert len(invocations) == 1
        assert invocations[0].status == "FAILED"
        assert invocations[0].error_class == "STRUCTURED_OUTPUT_INVALID"
        assert invocations[0].estimated_cost == Decimal("0.006000")
        assert invocations[0].input_tokens == 100
        assert budget is not None
        assert budget.estimated_cost == Decimal("0.0060")
        assert budget.deep_reasoning_used == 1
        assert assessment_count == 0
        assert evidence_count == 0
        assert breakpoint_count == 0
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_post_interview_budget_exhaustion_fails_unit_without_invocation_or_retry(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_committed_response_session(sessions)
        cleanup_user_ids.add(user_id)
        cleanup_concept_ids.add(concept_id)
        async with sessions() as session, session.begin():
            budget = await session.get(SessionBudget, session_id)
            assert budget is not None
            budget.deep_reasoning_used = budget.max_deep_reasoning_calls

        provider = FakeReasoningProvider(output_data={"findings": []})
        coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=AIGateway(
                settings=create_settings(env_file=tmp_path / ".env"),
                sessionmaker=sessions,
                provider=provider,
            ),
        )

        first = await coordinator.evaluate(session_id)
        second = await coordinator.evaluate(session_id)

        assert first.failed_units == 1
        assert second.failed_units == 1
        assert first.units[0].error_category == "BUDGET_EXHAUSTED"
        assert second.units[0].error_category == "BUDGET_EXHAUSTED"
        assert provider.calls == 0
        async with sessions() as session:
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == session_id)
            )
            assessment_count = await session.scalar(
                select(func.count())
                .select_from(Assessment)
                .where(Assessment.interview_session_id == session_id)
            )
            budget = await session.get(SessionBudget, session_id)
        assert invocation_count == 0
        assert assessment_count == 0
        assert budget is not None
        assert budget.deep_reasoning_used == budget.max_deep_reasoning_calls
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_two_live_calls_leave_capacity_for_seven_post_interview_units(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cleanup_user_ids: set[UUID] = set()
    cleanup_concept_ids: set[UUID] = set()
    try:
        async with sessions() as session, session.begin():
            dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
            concept = await _attach_problem_concept(
                session,
                dev.interview_session,
                key=f"stage5_browser_shape_{str(dev.interview_session.id).replace('-', '_')}",
            )
            cleanup_user_ids.add(dev.user.id)
            cleanup_concept_ids.add(concept.id)
            service = RealtimeControlService(session)
            for sequence in range(1, 8):
                await _candidate_turn(
                    service,
                    dev.interview_session.id,
                    sequence=sequence,
                    provider_item_id=f"browser-shape-{sequence}",
                    transcript=f"Independent bounded response {sequence}.",
                )
            await InterviewCompletionService(session).complete(
                session_id=dev.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=dev.interview_session.state_version,
                idempotency_key=f"browser-shape-complete:{dev.interview_session.id}",
            )
            session_id = dev.interview_session.id

        provider = FakeReasoningProvider(output_data={"findings": []})
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        for live_call in (1, 2):
            await gateway.reason_structured(
                interview_session_id=session_id,
                capability="STANDARD_REASONING",
                purpose="live_examiner",
                policy=assessment_evaluator_policy_descriptor(),
                instructions=ASSESSMENT_EVALUATOR_INSTRUCTIONS,
                input_content=f'{{"live_call":{live_call}}}',
                output_model=AssessmentAnalysisResult,
            )

        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessions,
            ai_gateway=gateway,
        ).evaluate(session_id)

        assert len(result.units) == 7
        assert result.completed_units == 7
        assert result.failed_units == 0
        assert provider.calls == 9
        async with sessions() as session:
            budget = await session.get(SessionBudget, session_id)
            invocations = list(
                await session.scalars(
                    select(AIInvocation).where(AIInvocation.interview_session_id == session_id)
                )
            )
        assert budget is not None
        assert budget.max_deep_reasoning_calls == 24
        assert budget.reserved_post_interview_deep_reasoning_calls == 16
        assert budget.deep_reasoning_used == 9
        assert len(invocations) == 9
        assert sum(item.purpose == "live_examiner" for item in invocations) == 2
        assert sum(item.purpose == "post_interview_assessment" for item in invocations) == 7
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=cleanup_user_ids,
            concept_ids=cleanup_concept_ids,
        )
        await engine.dispose()


async def test_malformed_output_exhausts_one_retry_and_provider_failure_does_not_retry(
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
            malformed_concept_key,
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
        assert provider.calls == 2
        async with sessions() as session:
            malformed_assessments = await session.scalar(
                select(func.count())
                .select_from(Assessment)
                .where(Assessment.interview_session_id == malformed_session_id)
            )
            malformed_evidence = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id == malformed_session_id)
            )
            malformed_breakpoints = await session.scalar(
                select(func.count())
                .select_from(Breakpoint)
                .where(Breakpoint.user_id == malformed_user_id)
            )
            malformed_invocations = list(
                await session.scalars(
                    select(AIInvocation).where(
                        AIInvocation.interview_session_id == malformed_session_id
                    )
                )
            )
            malformed_budget = await session.get(SessionBudget, malformed_session_id)
        assert malformed_assessments == 0
        assert malformed_evidence == 0
        assert malformed_breakpoints == 0
        assert len(malformed_invocations) == 2
        assert malformed_budget is not None
        assert malformed_budget.deep_reasoning_used == 2
        assert malformed_budget.estimated_cost == Decimal("0.0010")
        assert all(
            invocation.status == "FAILED"
            and invocation.error_class == "STRUCTURED_OUTPUT_INVALID"
            for invocation in malformed_invocations
        )
        for invocation in malformed_invocations:
            _assert_completed_provider_usage(invocation)

        provider.output_data = _analysis_output(concept_key=concept_key)
        provider.error = ReasoningProviderError("PROVIDER_UNAVAILABLE")
        failed = await coordinator.evaluate(failed_session_id)

        assert malformed.failed_units == 1
        assert malformed.units[0].error_category == "STRUCTURED_OUTPUT_INVALID"
        assert failed.failed_units == 1
        assert failed.units[0].error_category == "PROVIDER_UNAVAILABLE"
        assert provider.calls == 3
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
        assert len(invocation_statuses) == 3
        assert set(invocation_statuses) == {"FAILED"}

        provider.error = None
        provider.output_data = _analysis_output(concept_key=malformed_concept_key)
        retried_later = await coordinator.evaluate(malformed_session_id)
        assert retried_later.completed_units == 1
        assert provider.calls == 4
        async with sessions() as session:
            later_assessments = await session.scalar(
                select(func.count())
                .select_from(Assessment)
                .where(Assessment.interview_session_id == malformed_session_id)
            )
            later_evidence = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.interview_session_id == malformed_session_id)
            )
            later_budget = await session.get(SessionBudget, malformed_session_id)
        assert later_assessments == 1
        assert later_evidence == 1
        assert later_budget is not None
        assert later_budget.deep_reasoning_used == 3
        assert later_budget.estimated_cost == Decimal("0.0015")
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
        weakness_finding = cast(list[dict[str, Any]], output["findings"])[0]
        weakness_finding["breakpoint_subtype"] = "left_pointer_monotonicity"
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
        positive_finding["boundary_kind"] = "MEANINGFUL_TECHNICAL_BOUNDARY"
        positive_finding["breakpoint_subtype"] = "left_pointer_monotonicity"
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

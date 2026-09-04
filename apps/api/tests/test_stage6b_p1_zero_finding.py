from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_ai_gateway import FakeReasoningProvider
from test_stage5b_evidence_engine import (
    _analysis_output,
    _attach_problem_concept,
    _candidate_turn,
    _cleanup_committed_stage5_rows,
    _create_committed_response_session,
)

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningProviderError,
    ReasoningRequest,
)
from app.config.settings import create_settings
from app.db.session import build_engine
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.evidence.models import Assessment, AssessmentUnitEvaluation, Evidence
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewSession
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.models import OutboxEvent
from app.realtime.control_service import RealtimeControlService


class NoopReportService:
    async def generate(self, **_kwargs: object) -> None:
        raise AssertionError("Report generation is not expected in this test")


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int]] = []

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        self.calls.append((outbox_event_id, attempt))


class ZeroThenTransientByUnitProvider(FakeReasoningProvider):
    def __init__(self) -> None:
        super().__init__(output_data={"findings": []})
        self.calls_by_unit: dict[str, int] = {}
        self.first_unit_key: str | None = None

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        unit_key = cast(str, request.metadata["assessment_unit_key"])
        self.calls_by_unit[unit_key] = self.calls_by_unit.get(unit_key, 0) + 1
        if self.first_unit_key is None:
            self.first_unit_key = unit_key
        should_fail = unit_key != self.first_unit_key and self.calls_by_unit[unit_key] == 1
        self.error = ReasoningProviderError("TRANSIENT_PROVIDER") if should_fail else None
        try:
            return await super().reason_structured(
                request,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        finally:
            self.error = None


async def test_completed_zero_finding_unit_is_durably_reused(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_committed_response_session(sessions)
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(output_data={"findings": []})
        coordinator = _coordinator(sessions, provider, tmp_path)

        first = await coordinator.evaluate(session_id)
        second = await coordinator.evaluate(session_id)

        async with sessions() as session:
            completion = await session.scalar(
                select(AssessmentUnitEvaluation).where(
                    AssessmentUnitEvaluation.interview_session_id == session_id
                )
            )
            assessment_count = await _count(session, Assessment, session_id)
            evidence_count = await _count(session, Evidence, session_id)
        assert first.completed_units == 1
        assert first.units[0].assessment_ids == ()
        assert first.units[0].evidence_ids == ()
        assert first.units[0].breakpoint_ids == ()
        assert second.skipped_units == 1
        assert second.units[0].error_category == "ALREADY_EVALUATED"
        assert second.units[0].assessment_ids == ()
        assert second.units[0].evidence_ids == ()
        assert second.units[0].breakpoint_ids == ()
        assert completion is not None
        assert completion.finding_count == 0
        assert completion.unit_key == first.units[0].unit_key
        assert assessment_count == 0
        assert evidence_count == 0
        assert provider.calls == 1
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


async def test_nonzero_completion_marker_and_assessment_compatibility_fallback(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, concept_key, concept_id = (
            await _create_committed_response_session(sessions)
        )
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(
            output_data=_analysis_output(concept_key=concept_key)
        )
        coordinator = _coordinator(sessions, provider, tmp_path)

        first = await coordinator.evaluate(session_id)
        async with sessions() as session, session.begin():
            completion = await session.scalar(
                select(AssessmentUnitEvaluation).where(
                    AssessmentUnitEvaluation.interview_session_id == session_id
                )
            )
            assert completion is not None and completion.finding_count == 1
            await session.delete(completion)

        legacy_reuse = await coordinator.evaluate(session_id)

        assert first.completed_units == 1
        assert len(first.units[0].assessment_ids) == 1
        assert len(first.units[0].evidence_ids) == 1
        assert legacy_reuse.skipped_units == 1
        assert legacy_reuse.units[0].error_category == "ALREADY_EVALUATED"
        assert legacy_reuse.units[0].assessment_ids == first.units[0].assessment_ids
        assert legacy_reuse.units[0].evidence_ids == first.units[0].evidence_ids
        assert provider.calls == 1
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


@pytest.mark.parametrize(
    ("provider_factory", "expected_category", "expected_calls"),
    [
        (
            lambda: FakeReasoningProvider(output_data={"findings": [{"malformed": True}]}),
            "STRUCTURED_OUTPUT_INVALID",
            2,
        ),
        (
            lambda: FakeReasoningProvider(
                output_data={"findings": []},
                error=ReasoningProviderError("TRANSIENT_PROVIDER"),
            ),
            "TRANSIENT_PROVIDER",
            1,
        ),
    ],
)
async def test_failed_evaluation_does_not_create_completion_marker(
    tmp_path: Path,
    provider_factory: Callable[[], FakeReasoningProvider],
    expected_category: str,
    expected_calls: int,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_committed_response_session(sessions)
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = provider_factory()

        result = await _coordinator(sessions, provider, tmp_path).evaluate(session_id)

        async with sessions() as session:
            completion_count = await session.scalar(
                select(func.count())
                .select_from(AssessmentUnitEvaluation)
                .where(AssessmentUnitEvaluation.interview_session_id == session_id)
            )
        assert result.failed_units == 1
        assert result.units[0].error_category == expected_category
        assert completion_count == 0
        assert provider.calls == expected_calls
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


async def test_admission_failure_rolls_back_findings_and_completion_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, concept_key, concept_id = (
            await _create_committed_response_session(sessions)
        )
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(
            output_data=_analysis_output(concept_key=concept_key)
        )
        coordinator = _coordinator(sessions, provider, tmp_path)
        persist_finding = coordinator._persist_finding

        async def persist_then_fail(**kwargs: Any) -> Any:
            await persist_finding(**kwargs)
            raise RuntimeError("deterministic admission interrupted")

        monkeypatch.setattr(coordinator, "_persist_finding", persist_then_fail)
        failed = await coordinator.evaluate(session_id)

        async with sessions() as session:
            assessment_count = await _count(session, Assessment, session_id)
            evidence_count = await _count(session, Evidence, session_id)
            completion_count = await session.scalar(
                select(func.count())
                .select_from(AssessmentUnitEvaluation)
                .where(AssessmentUnitEvaluation.interview_session_id == session_id)
            )
        assert failed.failed_units == 1
        assert failed.units[0].error_category == "RuntimeError"
        assert assessment_count == 0
        assert evidence_count == 0
        assert completion_count == 0

        monkeypatch.setattr(coordinator, "_persist_finding", persist_finding)
        retried = await coordinator.evaluate(session_id)
        assert retried.completed_units == 1
        assert provider.calls == 2
        async with sessions() as session:
            assert await _count(session, Assessment, session_id) == 1
            assert await _count(session, Evidence, session_id) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AssessmentUnitEvaluation)
                    .where(AssessmentUnitEvaluation.interview_session_id == session_id)
                )
                == 1
            )
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


async def test_changed_unit_key_is_evaluated_independently(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_active_response_session(sessions)
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(output_data={"findings": []})
        coordinator = _coordinator(sessions, provider, tmp_path)

        first = await coordinator.evaluate_active_checkpoint(session_id)
        async with sessions() as session, session.begin():
            await _candidate_turn(
                RealtimeControlService(session),
                session_id,
                sequence=2,
                provider_item_id="zero-finding-second-turn",
                transcript="This is a distinct later stable assessment unit.",
            )
        second = await coordinator.evaluate_active_checkpoint(session_id)
        repeated_second = await coordinator.evaluate_active_checkpoint(session_id)

        async with sessions() as session:
            completions = list(
                await session.scalars(
                    select(AssessmentUnitEvaluation).where(
                        AssessmentUnitEvaluation.interview_session_id == session_id
                    )
                )
            )
        assert first.completed_units == 1
        assert second.completed_units == 1
        assert repeated_second.skipped_units == 1
        assert first.units[0].unit_key != second.units[0].unit_key
        assert {item.unit_key for item in completions} == {
            first.units[0].unit_key,
            second.units[0].unit_key,
        }
        assert provider.calls == 2
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


async def test_evaluator_policy_version_is_part_of_completion_identity(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    old_policy_id: UUID | None = None
    invocation_id: UUID | None = None
    current_policy_id: UUID | None = None
    try:
        session_id, user_id, _, concept_id = await _create_committed_response_session(sessions)
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(output_data={"findings": []})
        coordinator = _coordinator(sessions, provider, tmp_path)

        first = await coordinator.evaluate(session_id)
        async with sessions() as session, session.begin():
            completion = await session.scalar(
                select(AssessmentUnitEvaluation).where(
                    AssessmentUnitEvaluation.interview_session_id == session_id
                )
            )
            assert completion is not None
            invocation = await session.get(
                AIInvocation,
                completion.successful_ai_invocation_id,
            )
            assert invocation is not None
            invocation_id = invocation.id
            current_policy_id = invocation.ai_policy_version_id
            old_policy = AIPolicyVersion(
                policy_key="assessment_evaluator",
                version=f"v2-test-{session_id.hex}",
                configuration_json={},
            )
            session.add(old_policy)
            await session.flush()
            old_policy_id = old_policy.id
            completion.evaluator_policy_version_id = old_policy.id
            invocation.ai_policy_version_id = old_policy.id

        under_current_policy = await coordinator.evaluate(session_id)

        async with sessions() as session:
            completions = list(
                await session.scalars(
                    select(AssessmentUnitEvaluation).where(
                        AssessmentUnitEvaluation.interview_session_id == session_id
                    )
                )
            )
        assert first.completed_units == 1
        assert under_current_policy.completed_units == 1
        assert len(completions) == 2
        assert len({item.evaluator_policy_version_id for item in completions}) == 2
        assert provider.calls == 2
    finally:
        if invocation_id is not None and current_policy_id is not None:
            async with sessions() as session, session.begin():
                invocation = await session.get(AIInvocation, invocation_id)
                if invocation is not None:
                    invocation.ai_policy_version_id = current_policy_id
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        if old_policy_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(AIPolicyVersion).where(AIPolicyVersion.id == old_policy_id)
                )
        await engine.dispose()


async def test_active_zero_finding_checkpoint_is_reused_after_completion(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_active_response_session(sessions)
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        provider = FakeReasoningProvider(output_data={"findings": []})
        coordinator = _coordinator(sessions, provider, tmp_path)

        active = await coordinator.evaluate_active_checkpoint(session_id)
        repeated_active = await coordinator.evaluate_active_checkpoint(session_id)
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            await InterviewCompletionService(session).complete(
                session_id=session_id,
                reason="USER_ENDED",
                expected_state_version=interview.state_version,
                idempotency_key=f"zero-finding-active-complete:{session_id}",
            )
        completed = await coordinator.evaluate(session_id)

        assert active.completed_units == 1
        assert repeated_active.skipped_units == 1
        assert completed.skipped_units == 1
        assert repeated_active.units[0].error_category == "ALREADY_EVALUATED"
        assert completed.units[0].error_category == "ALREADY_EVALUATED"
        assert provider.calls == 1
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


async def test_outbox_retry_does_not_respend_successful_zero_finding_unit(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_ids: set[UUID] = set()
    concept_ids: set[UUID] = set()
    try:
        session_id, user_id, _, concept_id = await _create_active_response_session(
            sessions,
            transcripts=(
                "First stable unit has no material finding.",
                "Second stable unit transiently fails once.",
            ),
        )
        user_ids.add(user_id)
        concept_ids.add(concept_id)
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, session_id)
            assert interview is not None
            await InterviewCompletionService(session).complete(
                session_id=session_id,
                reason="USER_ENDED",
                expected_state_version=interview.state_version,
                idempotency_key=f"zero-finding-outbox-complete:{session_id}",
            )
        provider = ZeroThenTransientByUnitProvider()
        coordinator = _coordinator(sessions, provider, tmp_path)
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=coordinator,
            report_service=NoopReportService(),  # type: ignore[arg-type]
        )
        publisher = RecordingPublisher()
        dispatcher = OutboxDispatcher(sessionmaker=sessions, publisher=publisher)

        await dispatcher.dispatch_once()
        event_id, first_attempt = publisher.calls[-1]
        first = await consumer.consume(event_id, first_attempt)
        async with sessions() as session, session.begin():
            event = await session.get(OutboxEvent, event_id)
            assert event is not None and event.status == "RETRY"
            event.next_retry_at = event.created_at
        await dispatcher.dispatch_once()
        _, second_attempt = publisher.calls[-1]
        second = await consumer.consume(event_id, second_attempt)

        assert first.status == "RETRY"
        assert second.status == "COMPLETED"
        assert provider.first_unit_key is not None
        assert provider.calls_by_unit[provider.first_unit_key] == 1
        assert sorted(provider.calls_by_unit.values()) == [1, 2]
        async with sessions() as session:
            event = await session.get(OutboxEvent, event_id)
            completion_count = await session.scalar(
                select(func.count())
                .select_from(AssessmentUnitEvaluation)
                .where(AssessmentUnitEvaluation.interview_session_id == session_id)
            )
            report_event_count = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.interview_session_id == session_id,
                    OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                )
            )
        assert event is not None and event.status == "COMPLETED"
        assert completion_count == 2
        assert report_event_count == 1
    finally:
        await _cleanup_committed_stage5_rows(
            sessions,
            user_ids=user_ids,
            concept_ids=concept_ids,
        )
        await engine.dispose()


def _coordinator(
    sessions: async_sessionmaker[AsyncSession],
    provider: FakeReasoningProvider,
    tmp_path: Path,
) -> SessionEvidenceEvaluationCoordinator:
    return SessionEvidenceEvaluationCoordinator(
        sessionmaker=sessions,
        ai_gateway=AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        ),
    )


async def _create_active_response_session(
    sessions: async_sessionmaker[AsyncSession],
    *,
    transcripts: tuple[str, ...] = ("This is one stable active Coach assessment unit.",),
) -> tuple[UUID, UUID, str, UUID]:
    async with sessions() as session, session.begin():
        development = await create_development_interview(
            session,
            mode="COACH",
            initial_stage="IMPLEMENTATION",
        )
        development.budget.max_deep_reasoning_calls = 20
        concept_key = f"stage6b_zero_{development.interview_session.id.hex}"
        concept = await _attach_problem_concept(
            session,
            development.interview_session,
            key=concept_key,
        )
        control = RealtimeControlService(session)
        for sequence, transcript in enumerate(transcripts, start=1):
            await _candidate_turn(
                control,
                development.interview_session.id,
                sequence=sequence,
                provider_item_id=f"zero-finding-{sequence}-{development.interview_session.id}",
                transcript=transcript,
            )
        return (
            development.interview_session.id,
            development.user.id,
            concept_key,
            concept.id,
        )


async def _count(
    session: AsyncSession,
    model: type[Assessment] | type[Evidence],
    session_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.interview_session_id == session_id)
    )
    return int(value or 0)

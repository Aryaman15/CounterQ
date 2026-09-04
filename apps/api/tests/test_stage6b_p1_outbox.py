from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningProviderError,
    ReasoningRequest,
    ReasoningUsage,
)
from app.auth.models import User
from app.config.settings import create_settings
from app.db.session import build_engine
from app.evidence.coordinator import SessionEvaluationResult, UnitEvaluationResult
from app.evidence.models import AssessmentUnitEvaluation, Breakpoint, Evidence
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import SessionBudget
from app.outbox.consumer import ConsumerResult, PostSessionOutboxConsumer
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.models import OutboxEvent
from app.outbox.publisher import RQJobPublisher
from app.outbox.repository import OutboxRepository
from app.problems.models import Problem
from app.reports.models import SessionReport
from app.reports.service import SessionReportGenerationService, initial_report_generation_key


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int]] = []
        self.transaction_probe: Any | None = None

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        if self.transaction_probe is not None:
            assert self.transaction_probe() is False
        self.calls.append((outbox_event_id, attempt))


class BlockingEvidenceCoordinator:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return _successful_evaluation(interview_session_id)


class CountingEvidenceCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        self.calls += 1
        return _successful_evaluation(interview_session_id)


class ImmediateConsumerPublisher:
    def __init__(
        self,
        consumer: PostSessionOutboxConsumer,
        *,
        raise_after_consume: bool = False,
    ) -> None:
        self._consumer = consumer
        self._raise_after_consume = raise_after_consume
        self.calls: list[tuple[UUID, int]] = []
        self.results: list[ConsumerResult] = []

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        self.calls.append((outbox_event_id, attempt))
        self.results.append(await self._consumer.consume(outbox_event_id, attempt))
        if self._raise_after_consume:
            raise ConnectionError("enqueue acknowledgement was lost")


class ActiveConsumerPublisher:
    def __init__(
        self,
        consumer: PostSessionOutboxConsumer,
        *,
        started: asyncio.Event,
    ) -> None:
        self._consumer = consumer
        self._started = started
        self.calls: list[tuple[UUID, int]] = []
        self.task: asyncio.Task[ConsumerResult] | None = None

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        self.calls.append((outbox_event_id, attempt))
        self.task = asyncio.create_task(self._consumer.consume(outbox_event_id, attempt))
        await asyncio.wait_for(self._started.wait(), timeout=2)


class FailingPublisher:
    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        del outbox_event_id, attempt
        raise ConnectionError("enqueue failed before worker acknowledgement")


class OwnershipStealingEvidenceCoordinator:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        event_id: UUID,
    ) -> None:
        self._sessions = sessions
        self._event_id = event_id

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        async with self._sessions() as session, session.begin():
            event = await session.get(OutboxEvent, self._event_id)
            assert event is not None
            event.attempt_count += 1
            event.status = "PUBLISHED"
            event.next_retry_at = datetime.now(UTC) + timedelta(minutes=2)
        return _successful_evaluation(interview_session_id)


class NoopReportService:
    async def generate(self, **_kwargs: object) -> None:
        raise AssertionError("Report generation is not expected in this test")


class BlockingReportProvider:
    provider_name = "stage6b-p1-fake"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.active = 0
        self.maximum_active = 0

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        del request, reasoning_effort
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return ProviderReasoningResult(
                output_data=_insufficient_report_output(),
                provider=self.provider_name,
                model=model,
                provider_model_version="stage6b-p1-fake-v1",
                provider_request_id=f"stage6b-p1-{self.calls}",
                usage=ReasoningUsage(input_tokens=10, cached_input_tokens=0, output_tokens=10),
                latency_ms=1,
                retry_count=0,
                estimated_cost=Decimal("0.0001"),
                currency="USD",
            )
        finally:
            self.active -= 1


class CountingReportProvider(BlockingReportProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release.set()


class TimeoutReportProvider:
    provider_name = "stage6b-timeout-fake"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ReasoningRequest] = []

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        del model, reasoning_effort
        self.calls += 1
        self.requests.append(request)
        raise ReasoningProviderError("TIMEOUT", "Injected report timeout")


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def enqueue(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


@asynccontextmanager
async def completed_interview(
    sessions: async_sessionmaker[AsyncSession],
    *,
    key: str,
) -> AsyncIterator[UUID]:
    user_id: UUID | None = None
    problem_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session,
                initial_stage="IMPLEMENTATION",
            )
            user_id = development.user.id
            problem_id = development.problem.id
            await InterviewCompletionService(session).complete(
                session_id=development.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key=key,
            )
            session_id = development.interview_session.id
        yield session_id
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))


async def test_dispatch_publish_runs_after_claim_transaction_commits() -> None:
    engine = build_engine()
    tracked: list[AsyncSession] = []

    class TrackingSession(AsyncSession):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            tracked.append(self)

    sessions = cast(
        async_sessionmaker[AsyncSession],
        async_sessionmaker(engine, class_=TrackingSession, expire_on_commit=False),
    )
    try:
        async with completed_interview(sessions, key="p1-publish-boundary"):
            publisher = RecordingPublisher()
            publisher.transaction_probe = lambda: any(
                session.in_transaction() for session in tracked
            )
            result = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
            ).dispatch_once()
            assert result.published == 1
            assert len(publisher.calls) == 1
    finally:
        await engine.dispose()


async def test_normal_published_attempt_is_claimed_and_completed_once() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-normal-handoff") as session_id:
            coordinator = CountingEvidenceCoordinator()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=coordinator,  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
            )
            publisher = RecordingPublisher()

            dispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
            ).dispatch_once()
            event_id, attempt = publisher.calls[0]
            async with sessions() as session:
                published = await session.get(OutboxEvent, event_id)
                assert published is not None
                assert published.status == "PUBLISHED"
                assert published.published_at is not None

            completed = await consumer.consume(event_id, attempt)
            duplicate = await consumer.consume(event_id, attempt)

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                report_event_count = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == session_id,
                        OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                    )
                )
            assert dispatch.published == 1
            assert completed.status == "COMPLETED"
            assert duplicate.status == "SKIPPED"
            assert duplicate.category == "OUTBOX_OWNERSHIP_LOST"
            assert event is not None and event.status == "COMPLETED"
            assert coordinator.calls == 1
            assert report_event_count == 1
    finally:
        await engine.dispose()


async def test_fast_report_worker_completes_before_dispatcher_acknowledgement(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-fast-report-worker") as session_id:
            event_id = await _replace_evidence_event_with_report_event(sessions, session_id)
            provider = CountingReportProvider()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=cast(Any, None),
                report_service=SessionReportGenerationService(
                    sessionmaker=sessions,
                    ai_gateway=AIGateway(
                        settings=create_settings(env_file=tmp_path / ".env"),
                        sessionmaker=sessions,
                        provider=provider,
                    ),
                    reasoning_timeout_seconds=60.0,
                ),
            )
            publisher = ImmediateConsumerPublisher(consumer)

            dispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
            ).dispatch_once()

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                reports = list(
                    await session.scalars(
                        select(SessionReport).where(
                            SessionReport.interview_session_id == session_id
                        )
                    )
                )
            assert dispatch.published == 1
            assert dispatch.retryable == 0
            assert dispatch.failed == 0
            assert publisher.results[0].status == "COMPLETED"
            assert event is not None and event.status == "COMPLETED"
            assert event.published_at is not None
            assert len(reports) == 1
            assert reports[0].status == "READY"
            assert provider.calls == 1
            redispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
            ).dispatch_once()
            assert redispatch.claimed == 0
            assert provider.calls == 1
    finally:
        await engine.dispose()


async def test_dispatcher_acknowledgement_preserves_active_early_worker_lease() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = BlockingEvidenceCoordinator()
    publisher: ActiveConsumerPublisher | None = None
    try:
        async with completed_interview(sessions, key="p1-active-early-worker"):
            dispatch_now = datetime.now(UTC) + timedelta(seconds=1)
            worker_now = dispatch_now + timedelta(hours=1)
            worker_lease = worker_now + timedelta(seconds=120)
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=coordinator,  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
                clock=lambda: worker_now,
            )
            publisher = ActiveConsumerPublisher(consumer, started=coordinator.started)

            dispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
                claim_lease_seconds=10,
                clock=lambda: dispatch_now,
            ).dispatch_once()
            event_id, attempt = publisher.calls[0]
            async with sessions() as session:
                active = await session.get(OutboxEvent, event_id)
                assert active is not None
                assert active.status == "PROCESSING"
                assert active.published_at == worker_now
                assert active.next_retry_at == worker_lease

            duplicate = await consumer.consume(event_id, attempt)
            assert dispatch.published == 1
            assert duplicate.status == "SKIPPED"
            assert duplicate.category == "OUTBOX_OWNERSHIP_LOST"
            assert coordinator.calls == 1

            coordinator.release.set()
            assert publisher.task is not None
            completed = await publisher.task
            assert completed.status == "COMPLETED"
    finally:
        coordinator.release.set()
        if publisher is not None and publisher.task is not None:
            await publisher.task
        await engine.dispose()


async def test_ambiguous_publish_failure_does_not_clobber_completed_worker() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-ambiguous-publish") as session_id:
            coordinator = CountingEvidenceCoordinator()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=coordinator,  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
            )
            publisher = ImmediateConsumerPublisher(
                consumer,
                raise_after_consume=True,
            )

            dispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
            ).dispatch_once()
            event_id, _ = publisher.calls[0]

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                report_event_count = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == session_id,
                        OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                    )
                )
            assert dispatch.published == 1
            assert dispatch.retryable == 0
            assert dispatch.failed == 0
            assert publisher.results[0].status == "COMPLETED"
            assert event is not None and event.status == "COMPLETED"
            assert event.last_error is None
            assert coordinator.calls == 1
            assert report_event_count == 1
    finally:
        await engine.dispose()


async def test_publish_failure_without_worker_acknowledgement_remains_retryable() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(
            sessions, key="p1-unacknowledged-publish"
        ) as session_id:
            dispatch = await OutboxDispatcher(
                sessionmaker=sessions,
                publisher=FailingPublisher(),
            ).dispatch_once()

            async with sessions() as session:
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.interview_session_id == session_id
                    )
                )
            assert dispatch.published == 0
            assert dispatch.retryable == 1
            assert dispatch.failed == 0
            assert event is not None and event.status == "RETRY"
            assert event.published_at is None
            assert event.last_error == "ConnectionError"
    finally:
        await engine.dispose()


async def test_rq_publication_propagates_attempt_to_consumer() -> None:
    queue = RecordingQueue()
    publisher = RQJobPublisher.__new__(RQJobPublisher)
    cast(Any, publisher)._queue = queue
    event_id = UUID("00000000-0000-0000-0000-000000000123")

    await publisher.publish(outbox_event_id=event_id, attempt=7)

    assert queue.calls == [
        (
            ("app.worker.jobs.consume_outbox_event", str(event_id), 7),
            {
                "job_id": f"outbox-{event_id}-attempt-7",
                "result_ttl": 3600,
                "failure_ttl": 86400,
            },
        )
    ]


async def test_active_heartbeat_prevents_redispatch_after_original_lease() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-heartbeat-active") as session_id:
            publisher = RecordingPublisher()
            dispatcher = OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
                claim_lease_seconds=0.3,  # type: ignore[arg-type]
            )
            await dispatcher.dispatch_once()
            event_id, attempt = publisher.calls[0]
            coordinator = BlockingEvidenceCoordinator()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=coordinator,  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
                processing_lease_seconds=0.3,  # type: ignore[arg-type]
            )
            task = asyncio.create_task(consumer.consume(event_id, attempt))
            await asyncio.wait_for(coordinator.started.wait(), timeout=2)
            await asyncio.sleep(0.45)

            redispatch = await dispatcher.dispatch_once()
            assert redispatch.claimed == 0
            assert publisher.calls == [(event_id, attempt)]

            coordinator.release.set()
            result = await task
            assert result.status == "COMPLETED"
            async with sessions() as session:
                report_event_count = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == session_id,
                        OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                    )
                )
            assert report_event_count == 1
    finally:
        await engine.dispose()


async def test_stopped_heartbeat_allows_lease_recovery() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-heartbeat-stopped"):
            publisher = RecordingPublisher()
            dispatcher = OutboxDispatcher(
                sessionmaker=sessions,
                publisher=publisher,
                claim_lease_seconds=0.3,  # type: ignore[arg-type]
            )
            await dispatcher.dispatch_once()
            event_id, attempt = publisher.calls[0]
            coordinator = BlockingEvidenceCoordinator()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=coordinator,  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
                processing_lease_seconds=0.3,  # type: ignore[arg-type]
            )
            task = asyncio.create_task(consumer.consume(event_id, attempt))
            await asyncio.wait_for(coordinator.started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.35)

            recovered = await dispatcher.dispatch_once()
            assert recovered.published == 1
            assert publisher.calls[-1] == (event_id, attempt + 1)
    finally:
        await engine.dispose()


async def test_stale_evidence_attempt_cannot_complete_or_enqueue_report() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-stale-evidence") as session_id:
            publisher = RecordingPublisher()
            await OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once()
            event_id, attempt = publisher.calls[0]
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=OwnershipStealingEvidenceCoordinator(sessions, event_id),  # type: ignore[arg-type]
                report_service=NoopReportService(),  # type: ignore[arg-type]
            )

            result = await consumer.consume(event_id, attempt)

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                report_count = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == session_id,
                        OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                    )
                )
            assert result.status == "SKIPPED"
            assert result.category == "OUTBOX_OWNERSHIP_LOST"
            assert event is not None and event.status == "PUBLISHED"
            assert report_count == 0
    finally:
        await engine.dispose()


async def test_stale_report_worker_cannot_ready_and_recovery_reuses_report_version(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-stale-report") as session_id:
            event_id = await _replace_evidence_event_with_report_event(sessions, session_id)
            publisher = RecordingPublisher()
            await OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once()
            _, first_attempt = publisher.calls[-1]
            provider = BlockingReportProvider()
            service = SessionReportGenerationService(
                sessionmaker=sessions,
                ai_gateway=AIGateway(
                    settings=create_settings(env_file=tmp_path / ".env"),
                    sessionmaker=sessions,
                    provider=provider,
                ),
                reasoning_timeout_seconds=60.0,
            )
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=cast(Any, None),
                report_service=service,
            )
            stale_task = asyncio.create_task(consumer.consume(event_id, first_attempt))
            await asyncio.wait_for(provider.started.wait(), timeout=2)

            duplicate = await consumer.consume(event_id, first_attempt)
            assert duplicate.status == "SKIPPED"
            assert provider.maximum_active == 1
            async with sessions() as session, session.begin():
                event = await session.get(OutboxEvent, event_id)
                assert event is not None
                event.attempt_count += 1
                event.status = "PUBLISHED"
                event.next_retry_at = datetime.now(UTC) + timedelta(minutes=2)
                recovered_attempt = event.attempt_count
            provider.release.set()

            stale = await stale_task
            assert stale.status == "SKIPPED"
            async with sessions() as session:
                pending = await session.scalar(
                    select(SessionReport).where(
                        SessionReport.interview_session_id == session_id
                    )
                )
                assert pending is not None
                assert pending.report_version == 1
                assert pending.status == "GENERATING"

            recovered = await consumer.consume(event_id, recovered_attempt)
            assert recovered.status == "COMPLETED"
            async with sessions() as session:
                reports = list(
                    await session.scalars(
                        select(SessionReport).where(
                            SessionReport.interview_session_id == session_id
                        )
                    )
                )
            assert len(reports) == 1
            assert reports[0].report_version == 1
            assert reports[0].status == "READY"
            assert provider.calls == 2
            assert provider.maximum_active == 1
    finally:
        await engine.dispose()


async def test_report_budget_exhaustion_records_coherent_report_and_outbox_failure(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-report-budget-failure") as session_id:
            event_id = await _replace_evidence_event_with_report_event(sessions, session_id)
            async with sessions() as session, session.begin():
                budget = await session.get(SessionBudget, session_id)
                assert budget is not None
                budget.report_reasoning_used = budget.max_report_reasoning_calls
            publisher = RecordingPublisher()
            await OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once()
            _, attempt = publisher.calls[-1]
            provider = CountingReportProvider()
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=cast(Any, None),
                report_service=SessionReportGenerationService(
                    sessionmaker=sessions,
                    ai_gateway=AIGateway(
                        settings=create_settings(env_file=tmp_path / ".env"),
                        sessionmaker=sessions,
                        provider=provider,
                    ),
                    reasoning_timeout_seconds=60.0,
                ),
                max_attempts=1,
            )

            result = await consumer.consume(event_id, attempt)

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                report = await session.scalar(
                    select(SessionReport).where(
                        SessionReport.interview_session_id == session_id
                    )
                )
            assert result.status == "FAILED"
            assert result.category == "BUDGET_EXHAUSTED"
            assert event is not None and event.status == "FAILED"
            assert report is not None and report.status == "FAILED"
            assert report.last_failure_category == "BUDGET_EXHAUSTED"
            assert provider.calls == 0
    finally:
        await engine.dispose()


async def test_report_timeout_is_durably_retryable_without_inner_retry(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with completed_interview(sessions, key="p1-report-timeout") as session_id:
            event_id = await _replace_evidence_event_with_report_event(sessions, session_id)
            publisher = RecordingPublisher()
            await OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once()
            _, attempt = publisher.calls[-1]
            provider = TimeoutReportProvider()
            runtime_settings = create_settings(env_file=tmp_path / ".env")
            consumer = PostSessionOutboxConsumer(
                sessionmaker=sessions,
                evidence_coordinator=cast(Any, None),
                report_service=SessionReportGenerationService(
                    sessionmaker=sessions,
                    ai_gateway=AIGateway(
                        settings=runtime_settings,
                        sessionmaker=sessions,
                        provider=provider,
                    ),
                    reasoning_timeout_seconds=(
                        runtime_settings.session_report_reasoning_timeout_seconds
                    ),
                ),
            )

            result = await consumer.consume(event_id, attempt)

            async with sessions() as session:
                event = await session.get(OutboxEvent, event_id)
                report = await session.scalar(
                    select(SessionReport).where(
                        SessionReport.interview_session_id == session_id
                    )
                )
                budget = await session.get(SessionBudget, session_id)
                invocation = await session.scalar(
                    select(AIInvocation).where(
                        AIInvocation.interview_session_id == session_id,
                        AIInvocation.purpose == "session_report",
                    )
                )
                evidence_count = await session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                )
                breakpoint_count = await session.scalar(
                    select(func.count())
                    .select_from(Breakpoint)
                    .where(Breakpoint.first_detected_session_id == session_id)
                )
                unit_evaluation_count = await session.scalar(
                    select(func.count())
                    .select_from(AssessmentUnitEvaluation)
                    .where(AssessmentUnitEvaluation.interview_session_id == session_id)
                )
            assert result.status == "RETRY"
            assert result.category == "TIMEOUT"
            assert event is not None and event.status == "RETRY"
            assert event.next_retry_at is not None
            assert report is not None and report.status == "FAILED"
            assert report.last_failure_category == "TIMEOUT"
            assert report.structured_report_json is None
            assert budget is not None
            assert budget.max_report_reasoning_calls == 4
            assert budget.report_reasoning_used == 1
            assert budget.deep_reasoning_used == 0
            assert invocation is not None and invocation.status == "TIMED_OUT"
            assert provider.calls == 1
            assert provider.requests[0].timeout_seconds == 60.0
            assert evidence_count == 0
            assert breakpoint_count == 0
            assert unit_evaluation_count == 0
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(AIInvocation).where(
                        AIInvocation.interview_session_id == session_id
                    )
                )
    finally:
        await engine.dispose()


async def _replace_evidence_event_with_report_event(
    sessions: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> UUID:
    now = datetime.now(UTC)
    request_key = initial_report_generation_key(session_id)
    async with sessions() as session, session.begin():
        evidence_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.interview_session_id == session_id,
                OutboxEvent.event_type == "FINALIZE_SESSION_EVIDENCE",
            )
        )
        assert evidence_event is not None
        evidence_event.status = "COMPLETED"
        evidence_event.completed_at = now
        evidence_event.next_retry_at = None
        report_event, _ = await OutboxRepository(session).enqueue(
            aggregate_type="InterviewSession",
            aggregate_id=session_id,
            interview_session_id=session_id,
            event_type="GENERATE_SESSION_REPORT",
            payload={
                "interview_session_id": str(session_id),
                "generation_request_key": request_key,
                "report_policy": "session_report.v2",
            },
            deduplication_key=request_key,
            available_at=now,
            source_watermark=2,
        )
        return report_event.id


def _successful_evaluation(interview_session_id: UUID) -> SessionEvaluationResult:
    return SessionEvaluationResult(
        interview_session_id=interview_session_id,
        units=(
            UnitEvaluationResult(
                unit_key="p1-unit",
                unit_kind="CANDIDATE_RESPONSE",
                status="COMPLETED",
                error_category=None,
            ),
        ),
    )


def _insufficient_report_output() -> dict[str, object]:
    finding: dict[str, object] = {
        "title": "Limited session evidence",
        "finding": "This session did not produce enough evidence for a detailed conclusion.",
        "evidence_ids": [],
        "breakpoint_id": None,
        "independence_level": None,
        "based_on_insufficient_evidence": True,
    }
    insufficient = {
        "status": "INSUFFICIENT_EVIDENCE",
        "items": [],
        "insufficient_evidence_message": "Not enough evidence from this session.",
    }
    return {
        "contract_version": "session-report-output.v1",
        "summary": [finding],
        "strengths": [],
        "breakpoints": [],
        "claim_defense": insufficient,
        "correctness_implementation": insufficient,
        "complexity": insufficient,
        "edge_cases": insufficient,
        "debugging": insufficient,
        "adaptability": insufficient,
        "coach_assistance": [],
        "next_actions": [
            {
                "action": "Complete another interview to produce more diagnostic evidence.",
                "evidence_ids": [],
                "breakpoint_ids": [],
                "based_on_insufficient_evidence": True,
            }
        ],
    }

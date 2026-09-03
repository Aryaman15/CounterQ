from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_stage1_1a_persistence import create_stage1_graph
from test_stage1_1b_causal_persistence import add_transcript_segment
from test_stage5a_canonical_evaluation import evidence_fixture, validate_evidence

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
from app.evidence.coordinator import SessionEvaluationResult, UnitEvaluationResult
from app.evidence.models import Evidence
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import InterviewSession
from app.observation.models import InterviewEvent
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.models import OutboxEvent
from app.problems.models import Concept, Problem
from app.reports.models import SessionReport
from app.reports.routes import session_report_status
from app.reports.schema import ReportFinding, SessionReportSynthesis
from app.reports.service import (
    SessionReportGenerationError,
    SessionReportGenerationService,
    initial_report_generation_key,
)
from app.reports.source import SessionReportSourceBuilder
from app.reports.validator import SessionReportValidationError, SessionReportValidator


class ReportProvider:
    provider_name = "stage6b-fake"

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls = 0
        self.requests: list[ReasoningRequest] = []
        self.transaction_probe: Callable[[], bool] | None = None
        self.before_return: Callable[[], Awaitable[None]] | None = None

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        if self.transaction_probe is not None:
            assert self.transaction_probe() is False
        if self.before_return is not None:
            callback = self.before_return
            self.before_return = None
            await callback()
        self.calls += 1
        self.requests.append(request)
        return ProviderReasoningResult(
            output_data=self.output,
            provider=self.provider_name,
            model=model,
            provider_model_version="stage6b-fake-v1",
            provider_request_id=f"stage6b-request-{self.calls}",
            usage=ReasoningUsage(input_tokens=20, cached_input_tokens=0, output_tokens=30),
            latency_ms=3,
            retry_count=0,
            estimated_cost=Decimal("0.0002"),
            currency="USD",
        )


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[UUID, int]] = []

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        self.calls.append((outbox_event_id, attempt))
        if self.fail:
            raise ConnectionError("injected Redis outage")


class StubEvidenceCoordinator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        self.calls += 1
        return SessionEvaluationResult(
            interview_session_id=interview_session_id,
            units=(
                UnitEvaluationResult(
                    unit_key="stage6b-unit",
                    unit_kind="CANDIDATE_RESPONSE",
                    status="FAILED" if self.fail else "COMPLETED",
                    error_category="PROVIDER_TIMEOUT" if self.fail else None,
                ),
            ),
        )


class StubReportService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def generate(self, *, interview_session_id: UUID, generation_request_key: str) -> None:
        self.calls.append((interview_session_id, generation_request_key))


def _fixture(fixture_id: str):  # type: ignore[no-untyped-def]
    from app.evals.reports.corpus import load_report_corpus

    return next(item for item in load_report_corpus() if item.fixture_id == fixture_id)


async def test_completion_persists_factual_event_and_initial_outbox_atomically(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = InterviewCompletionService(db_session)

    first = await service.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage6b-complete",
    )
    repeated = await service.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=2,
        idempotency_key="stage6b-complete",
    )

    events = list(
        await db_session.scalars(
            select(InterviewEvent).where(
                InterviewEvent.interview_session_id == development.interview_session.id,
                InterviewEvent.event_type == "SESSION_COMPLETED",
            )
        )
    )
    outbox = list(
        await db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.interview_session_id == development.interview_session.id
            )
        )
    )
    assert first.session_completed_event_id == repeated.session_completed_event_id
    assert len(events) == 1
    assert len(outbox) == 1
    assert outbox[0].event_type == "FINALIZE_SESSION_EVIDENCE"
    assert outbox[0].source_watermark == events[0].server_sequence
    preparing = await session_report_status(development.interview_session.id, db_session)
    repeated_get = await session_report_status(development.interview_session.id, db_session)
    assert preparing.status == repeated_get.status == "PREPARING"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.interview_session_id == development.interview_session.id)
        )
        == 1
    )
    outbox[0].status = "FAILED"
    outbox[0].last_error = "PROVIDER_TIMEOUT"
    failed = await session_report_status(development.interview_session.id, db_session)
    assert failed.status == "FAILED"
    assert "saved" in failed.message


async def test_completion_rollback_removes_completion_and_outbox_intention(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    session_id = development.interview_session.id
    nested = await db_session.begin_nested()
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage6b-rollback",
    )
    await nested.rollback()
    db_session.expire_all()

    interview = await db_session.get(InterviewSession, session_id)
    outbox_count = await db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.interview_session_id == session_id)
    )
    assert interview is not None
    assert interview.status == "ACTIVE"
    assert outbox_count == 0


async def test_source_builder_excludes_invalidated_evidence(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture, polarity="POSITIVE")
    evidence.validation_status = "INVALIDATED"
    evidence.invalidated_at = datetime.now(UTC)
    evidence.invalidation_reason = "Superseded by deterministic test source correction."
    fixture.graph.interview_session.last_server_sequence = 1
    fixture.graph.interview_session.current_stage = "IMPLEMENTATION"
    await InterviewCompletionService(db_session).complete(
        session_id=fixture.graph.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage6b-invalidated",
    )

    bundle = await SessionReportSourceBuilder(db_session).build(fixture.graph.interview_session.id)

    assert bundle.evidence == []
    assert bundle.breakpoints == []


async def test_source_builder_excludes_starter_editor_baseline_evidence(
    db_session: AsyncSession,
) -> None:
    fixture = await evidence_fixture(db_session)
    await validate_evidence(db_session, fixture, polarity="POSITIVE")
    fixture.event.event_type = "CODE_SNAPSHOT_CREATED"
    fixture.event.payload = {"trigger": "INITIAL_EDITOR_STATE"}
    fixture.graph.interview_session.last_server_sequence = 1
    fixture.graph.interview_session.current_stage = "IMPLEMENTATION"
    await InterviewCompletionService(db_session).complete(
        session_id=fixture.graph.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage6b-starter",
    )

    bundle = await SessionReportSourceBuilder(db_session).build(fixture.graph.interview_session.id)

    assert bundle.evidence == []


async def test_source_builder_uses_actual_delivery_and_excludes_undelivered_prompt(
    db_session: AsyncSession,
) -> None:
    graph = await create_stage1_graph(db_session)
    event, actual_segment = await add_transcript_segment(
        db_session,
        graph,
        server_sequence=1,
        text="Only the wording the candidate actually heard.",
        speaker="COUNTERQ",
    )
    interactions = InterviewInteractionRepository(db_session)
    delivered = await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="CLARIFICATION",
        intent="Longer intended wording that was not fully delivered.",
        status="DELIVERED",
    )
    await interactions.add_delivery(
        interview_session_id=graph.interview_session.id,
        interviewer_prompt_id=delivered.id,
        delivery_attempt=1,
        intended_text="Longer intended wording that was not fully delivered.",
        actual_transcript_segment_id=actual_segment.id,
        delivery_state="DELIVERED",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    await interactions.add_prompt(
        interview_session_id=graph.interview_session.id,
        origin="SYSTEM",
        kind="CLARIFICATION",
        intent="Stale wording the candidate never heard.",
        status="STALE",
    )
    graph.interview_session.last_server_sequence = event.server_sequence
    graph.interview_session.current_stage = "IMPLEMENTATION"
    await InterviewCompletionService(db_session).complete(
        session_id=graph.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage6b-actual-delivery",
    )

    bundle = await SessionReportSourceBuilder(db_session).build(graph.interview_session.id)

    assert len(bundle.delivered_prompts) == 1
    assert bundle.delivered_prompts[0].prompt_id == delivered.id
    assert bundle.delivered_prompts[0].actual_text == actual_segment.text
    assert "Longer intended" not in bundle.serialize_for_ai()
    assert "Stale wording" not in bundle.serialize_for_ai()


@pytest.mark.parametrize("kind", ["foreign", "invalidated"])
def test_validator_rejects_evidence_not_in_active_session_allowlist(kind: str) -> None:
    fixture = _fixture("strong-independent-solution")
    invented = uuid4()
    summary = fixture.report.summary[0].model_copy(update={"evidence_ids": [invented]})
    report = fixture.report.model_copy(update={"summary": [summary]})

    with pytest.raises(SessionReportValidationError) as rejected:
        SessionReportValidator().validate(bundle=fixture.bundle, report=report)

    assert "INVALID_EVIDENCE_REFERENCE" in {issue.category for issue in rejected.value.issues}
    assert kind in {"foreign", "invalidated"}


def test_validator_rejects_invented_and_unrelated_breakpoints() -> None:
    fixture = _fixture("independent-misconception-unresolved")
    item = fixture.report.breakpoints[0]
    invented = item.model_copy(update={"breakpoint_id": uuid4()})

    with pytest.raises(SessionReportValidationError) as invalid:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"breakpoints": [invented]}),
        )
    assert "INVALID_BREAKPOINT_REFERENCE" in {issue.category for issue in invalid.value.issues}

    source = fixture.bundle.breakpoints[0].model_copy(update={"supporting_evidence_ids": []})
    with pytest.raises(SessionReportValidationError) as unrelated:
        SessionReportValidator().validate(
            bundle=fixture.bundle.model_copy(update={"breakpoints": [source]}),
            report=fixture.report,
        )
    assert "BREAKPOINT_EVIDENCE_MISMATCH" in {issue.category for issue in unrelated.value.issues}


def test_validator_rejects_independence_overstatement_and_probe_as_hint() -> None:
    assisted = _fixture("coach-light-hint-assisted-correction")
    overstated = assisted.report.summary[0].model_copy(update={"independence_level": "INDEPENDENT"})
    with pytest.raises(SessionReportValidationError) as invalid:
        SessionReportValidator().validate(
            bundle=assisted.bundle,
            report=assisted.report.model_copy(update={"summary": [overstated]}),
        )
    assert "INDEPENDENCE_OVERSTATEMENT" in {issue.category for issue in invalid.value.issues}

    probed = _fixture("misconception-corrected-after-probe")
    mislabelled = probed.report.summary[0].model_copy(
        update={"finding": "You corrected this after a helpful hint."}
    )
    with pytest.raises(SessionReportValidationError) as invalid_probe:
        SessionReportValidator().validate(
            bundle=probed.bundle,
            report=probed.report.model_copy(update={"summary": [mislabelled]}),
        )
    assert "PROBE_MISLABELLED_AS_HINT" in {issue.category for issue in invalid_probe.value.issues}


def test_validator_rejects_undelivered_assistance_and_unsupported_recommendation() -> None:
    fixture = _fixture("coach-light-hint-assisted-correction")
    assistance = fixture.report.coach_assistance[0].model_copy(update={"delivery_ids": [uuid4()]})
    unsupported = fixture.report.next_actions[0].model_copy(
        update={
            "evidence_ids": [],
            "breakpoint_ids": [],
            "based_on_insufficient_evidence": False,
        }
    )
    report = fixture.report.model_copy(
        update={"coach_assistance": [assistance], "next_actions": [unsupported]}
    )

    with pytest.raises(SessionReportValidationError) as rejected:
        SessionReportValidator().validate(bundle=fixture.bundle, report=report)

    categories = {issue.category for issue in rejected.value.issues}
    assert "UNDELIVERED_ASSISTANCE_CLAIM" in categories
    assert "UNSUPPORTED_RECOMMENDATION" in categories


def test_validator_rejects_assistance_type_or_candidate_label_drift() -> None:
    fixture = _fixture("coach-light-hint-assisted-correction")
    assistance = fixture.report.coach_assistance[0].model_copy(
        update={"assistance_type": "DIRECT_TEACHING", "assistance_label": "Helpful hint"}
    )

    with pytest.raises(SessionReportValidationError) as rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"coach_assistance": [assistance]}),
        )

    categories = {issue.category for issue in rejected.value.issues}
    assert "ASSISTANCE_TYPE_MISMATCH" in categories
    assert "ASSISTANCE_LABEL_MISMATCH" in categories

    delivery = fixture.bundle.delivered_assistance[0].model_copy(
        update={"target_concept_id": uuid4()}
    )
    with pytest.raises(SessionReportValidationError) as target_rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle.model_copy(update={"delivered_assistance": [delivery]}),
            report=fixture.report,
        )
    assert "ASSISTANCE_TARGET_MISMATCH" in {
        issue.category for issue in target_rejected.value.issues
    }

    verification = fixture.report.coach_assistance[0].model_copy(
        update={"independent_verification_missing": False}
    )
    with pytest.raises(SessionReportValidationError) as verification_rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"coach_assistance": [verification]}),
        )
    assert "ASSISTANCE_VERIFICATION_MISMATCH" in {
        issue.category for issue in verification_rejected.value.issues
    }


def test_validator_keeps_assisted_evidence_out_of_independent_strengths() -> None:
    fixture = _fixture("coach-light-hint-assisted-correction")
    assisted_finding = fixture.report.summary[0]

    with pytest.raises(SessionReportValidationError) as rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"strengths": [assisted_finding]}),
        )

    assert "ASSISTED_EVIDENCE_IN_INDEPENDENT_STRENGTH" in {
        issue.category for issue in rejected.value.issues
    }


@pytest.mark.parametrize(
    ("copy", "category"),
    [
        ("Your interview score is 8 / 10.", "NUMERIC_SCORE"),
        ("Your personality is highly extroverted.", "PERSONALITY_JUDGMENT"),
        ("You should be hired after this session.", "HIRING_PREDICTION"),
    ],
)
def test_validator_rejects_forbidden_candidate_judgments(copy: str, category: str) -> None:
    fixture = _fixture("strong-independent-solution")
    finding = fixture.report.summary[0].model_copy(update={"finding": copy})

    with pytest.raises(SessionReportValidationError) as rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"summary": [finding]}),
        )

    assert category in {issue.category for issue in rejected.value.issues}


@pytest.mark.parametrize(
    "fixture_id",
    [
        "strong-independent-solution",
        "coach-light-hint-assisted-correction",
        "assisted-success-open-breakpoint",
        "little-evidence-for-edge-cases",
    ],
)
def test_validator_accepts_required_safe_report_shapes(fixture_id: str) -> None:
    fixture = _fixture(fixture_id)
    SessionReportValidator().validate(bundle=fixture.bundle, report=fixture.report)


async def test_dispatcher_claim_retry_backoff_lease_recovery_and_failure_are_durable() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, initial_stage="IMPLEMENTATION"
            )
            user_id = development.user.id
            problem_id = development.problem.id
            await InterviewCompletionService(session).complete(
                session_id=development.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage6b-dispatch",
            )
        publisher = RecordingPublisher()
        first, second = await asyncio.gather(
            OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once(),
            OutboxDispatcher(sessionmaker=sessions, publisher=publisher).dispatch_once(),
        )
        assert first.published + second.published == 1
        assert len(publisher.calls) == 1

        async with sessions() as session, session.begin():
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == development.interview_session.id
                )
            )
            assert event is not None
            event.status = "PROCESSING"
            event.next_retry_at = event.created_at
        recovered = await OutboxDispatcher(
            sessionmaker=sessions, publisher=publisher
        ).dispatch_once()
        assert recovered.published == 1
        assert len(publisher.calls) == 2

        async with sessions() as session, session.begin():
            event = await session.get(OutboxEvent, event.id)
            assert event is not None
            event.status = "RETRY"
            event.next_retry_at = event.created_at
        failing = RecordingPublisher(fail=True)
        retry = await OutboxDispatcher(
            sessionmaker=sessions, publisher=failing, max_attempts=4
        ).dispatch_once()
        assert retry.retryable == 1
        async with sessions() as session, session.begin():
            retryable = await session.get(OutboxEvent, event.id)
            assert retryable is not None
            assert retryable.status == "RETRY"
            assert retryable.attempt_count == 3
            assert retryable.last_attempt_at is not None
            assert retryable.next_retry_at is not None
            assert retryable.next_retry_at > retryable.last_attempt_at
            assert retryable.last_error == "ConnectionError"
            retryable.next_retry_at = retryable.created_at

        exhausted = await OutboxDispatcher(
            sessionmaker=sessions, publisher=failing, max_attempts=4
        ).dispatch_once()
        assert exhausted.failed == 1
        async with sessions() as session:
            failed = await session.get(OutboxEvent, event.id)
            assert failed is not None
            assert failed.status == "FAILED"
            assert failed.attempt_count == 4
            assert failed.next_retry_at is None
            assert failed.last_error == "ConnectionError"
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()


async def test_evidence_consumer_orders_report_work_and_is_idempotent() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, initial_stage="IMPLEMENTATION"
            )
            user_id = development.user.id
            problem_id = development.problem.id
            await InterviewCompletionService(session).complete(
                session_id=development.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage6b-consumer-order",
            )
            evidence_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == development.interview_session.id,
                    OutboxEvent.event_type == "FINALIZE_SESSION_EVIDENCE",
                )
            )
            assert evidence_event is not None
            evidence_event_id = evidence_event.id
            session_id = development.interview_session.id
        evidence_coordinator = StubEvidenceCoordinator()
        report_service = StubReportService()
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=evidence_coordinator,  # type: ignore[arg-type]
            report_service=report_service,  # type: ignore[arg-type]
        )

        finalized = await consumer.consume(evidence_event_id)
        duplicate_finalization = await consumer.consume(evidence_event_id)
        async with sessions() as session:
            report_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == session_id,
                    OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                )
            )
            assert report_event is not None
            report_event_id = report_event.id
        generated = await consumer.consume(report_event_id)
        duplicate_report = await consumer.consume(report_event_id)

        assert finalized.status == "COMPLETED"
        assert duplicate_finalization.status == "SKIPPED"
        assert generated.status == "COMPLETED"
        assert duplicate_report.status == "SKIPPED"
        assert evidence_coordinator.calls == 1
        assert len(report_service.calls) == 1
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()


async def test_failed_evidence_finalization_blocks_report_until_successful_retry() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session, initial_stage="IMPLEMENTATION"
            )
            user_id = development.user.id
            problem_id = development.problem.id
            await InterviewCompletionService(session).complete(
                session_id=development.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage6b-consumer-retry",
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == development.interview_session.id,
                    OutboxEvent.event_type == "FINALIZE_SESSION_EVIDENCE",
                )
            )
            assert event is not None
            event_id = event.id
            session_id = development.interview_session.id
        coordinator = StubEvidenceCoordinator(fail=True)
        report_service = StubReportService()
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=coordinator,  # type: ignore[arg-type]
            report_service=report_service,  # type: ignore[arg-type]
        )

        failed = await consumer.consume(event_id)
        async with sessions() as session:
            report_count = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.interview_session_id == session_id,
                    OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                )
            )
        assert failed.status == "RETRY"
        assert report_count == 0

        coordinator.fail = False
        retry_consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=coordinator,  # type: ignore[arg-type]
            report_service=report_service,  # type: ignore[arg-type]
            clock=lambda: datetime.now(UTC).replace(year=2099),
        )
        succeeded = await retry_consumer.consume(event_id)
        async with sessions() as session:
            report_count = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.interview_session_id == session_id,
                    OutboxEvent.event_type == "GENERATE_SESSION_REPORT",
                )
            )
        assert succeeded.status == "COMPLETED"
        assert report_count == 1
        assert report_service.calls == []
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()


async def test_report_generation_is_idempotent_versioned_and_exactly_provenanced(
    tmp_path: Path,
) -> None:
    engine = build_engine()
    tracked_sessions: list[AsyncSession] = []

    class TrackingSession(AsyncSession):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            tracked_sessions.append(self)

    sessions = cast(
        async_sessionmaker[AsyncSession],
        async_sessionmaker(engine, class_=TrackingSession, expire_on_commit=False),
    )
    user_id: UUID | None = None
    problem_id: UUID | None = None
    concept_id: UUID | None = None
    invocation_ids: list[UUID] = []
    try:
        async with sessions() as session, session.begin():
            fixture = await evidence_fixture(session)
            evidence = await validate_evidence(
                session,
                fixture,
                polarity="POSITIVE",
                finding="The candidate independently defended the expected lookup bound.",
            )
            fixture.graph.interview_session.last_server_sequence = 1
            fixture.graph.interview_session.current_stage = "IMPLEMENTATION"
            await InterviewCompletionService(session).complete(
                session_id=fixture.graph.interview_session.id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage6b-report-service",
            )
            session_id = fixture.graph.interview_session.id
            user_id = fixture.graph.user.id
            problem_id = fixture.graph.problem.id
            concept_id = fixture.concept.id
        provider = ReportProvider(_valid_report_output(evidence.id))
        gateway = AIGateway(
            settings=create_settings(env_file=tmp_path / ".env"),
            sessionmaker=sessions,
            provider=provider,
        )
        provider.transaction_probe = lambda: any(
            tracked.in_transaction() for tracked in tracked_sessions
        )
        service = SessionReportGenerationService(
            sessionmaker=sessions,
            ai_gateway=gateway,
        )

        first = await service.generate(
            interview_session_id=session_id,
            generation_request_key=initial_report_generation_key(session_id),
        )
        duplicate = await service.generate(
            interview_session_id=session_id,
            generation_request_key=initial_report_generation_key(session_id),
        )
        regenerated = await service.generate(
            interview_session_id=session_id,
            generation_request_key=f"session-report:{session_id}:session_report.v1:manual-2",
        )
        assert first.created is True
        assert duplicate.created is False
        assert duplicate.report_id == first.report_id
        assert regenerated.report_version == 2
        assert provider.calls == 2
        assert all(request.purpose == "session_report" for request in provider.requests)
        assert all(
            "session-report-input.v1" in request.input_content for request in provider.requests
        )
        async with sessions() as session:
            reports = list(
                await session.scalars(
                    select(SessionReport)
                    .where(SessionReport.interview_session_id == session_id)
                    .order_by(SessionReport.report_version)
                )
            )
            assert [(item.report_version, item.status, item.is_current) for item in reports] == [
                (1, "SUPERSEDED", False),
                (2, "READY", True),
            ]
            invocation = await session.get(AIInvocation, reports[1].generation_ai_invocation_id)
            assert invocation is not None
            assert invocation.purpose == "session_report"
            assert invocation.ai_policy_version_id == reports[1].generation_policy_version_id
            assert reports[1].structured_report_json is not None
            document = reports[1].structured_report_json
            assert document["contract_version"] == "session-report-output.v1"
            candidate_response = await session_report_status(session_id, session)
            assert candidate_response.status == "READY"
            assert candidate_response.report_id == reports[1].id
            assert candidate_response.report is not None

        invalid_output = _valid_report_output(evidence.id)
        invalid_summary = invalid_output["summary"]
        assert isinstance(invalid_summary, list)
        assert isinstance(invalid_summary[0], dict)
        invalid_summary[0]["finding"] = "Your interview score is 8 / 10."
        provider.output = invalid_output
        with pytest.raises(SessionReportGenerationError) as rejected:
            await service.generate(
                interview_session_id=session_id,
                generation_request_key=(f"session-report:{session_id}:session_report.v1:invalid-3"),
            )
        assert rejected.value.category == "REPORT_VALIDATION_FAILED"
        async with sessions() as session:
            reports = list(
                await session.scalars(
                    select(SessionReport)
                    .where(SessionReport.interview_session_id == session_id)
                    .order_by(SessionReport.report_version)
                )
            )
            assert reports[1].status == "READY" and reports[1].is_current is True
            assert reports[2].status == "FAILED" and reports[2].is_current is False
            assert reports[2].structured_report_json is None

        async def mutate_canonical_source() -> None:
            async with sessions() as mutation_session, mutation_session.begin():
                current_evidence = await mutation_session.get(Evidence, evidence.id)
                assert current_evidence is not None
                current_evidence.finding = (
                    "The canonical finding changed while report synthesis was in flight."
                )

        provider.output = _valid_report_output(evidence.id)
        provider.before_return = mutate_canonical_source
        with pytest.raises(SessionReportGenerationError) as stale:
            await service.generate(
                interview_session_id=session_id,
                generation_request_key=f"session-report:{session_id}:session_report.v1:stale-4",
            )
        assert stale.value.category == "SOURCE_CHANGED"
        async with sessions() as session:
            reports = list(
                await session.scalars(
                    select(SessionReport)
                    .where(SessionReport.interview_session_id == session_id)
                    .order_by(SessionReport.report_version)
                )
            )
            assert reports[1].status == "READY" and reports[1].is_current is True
            assert reports[3].status == "SUPERSEDED" and reports[3].is_current is False
            invocation_ids.extend(
                await session.scalars(
                    select(AIInvocation.id).where(
                        AIInvocation.interview_session_id == session_id,
                        AIInvocation.purpose == "session_report",
                    )
                )
            )
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if invocation_ids:
                    await session.execute(
                        delete(AIInvocation).where(AIInvocation.id.in_(invocation_ids))
                    )
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
                if concept_id is not None:
                    await session.execute(
                        delete(Concept).where(
                            Concept.id == concept_id,
                            Concept.description == "Canonical deterministic Stage 5 test concept.",
                        )
                    )
        await engine.dispose()


def _valid_report_output(evidence_id: UUID) -> dict[str, object]:
    finding = {
        "title": "Expected lookup behavior was defended",
        "finding": "You independently distinguished expected lookup time from a guarantee.",
        "evidence_ids": [str(evidence_id)],
        "breakpoint_id": None,
        "independence_level": "INDEPENDENT",
        "based_on_insufficient_evidence": False,
    }
    insufficient = {
        "status": "INSUFFICIENT_EVIDENCE",
        "items": [],
        "insufficient_evidence_message": "Not enough evidence from this session.",
    }
    return {
        "contract_version": "session-report-output.v1",
        "summary": [finding],
        "strengths": [finding],
        "breakpoints": [],
        "claim_defense": {
            "status": "SUPPORTED",
            "items": [finding],
            "insufficient_evidence_message": None,
        },
        "correctness_implementation": {
            "status": "SUPPORTED",
            "items": [finding],
            "insufficient_evidence_message": None,
        },
        "complexity": {
            "status": "SUPPORTED",
            "items": [finding],
            "insufficient_evidence_message": None,
        },
        "edge_cases": insufficient,
        "debugging": insufficient,
        "adaptability": insufficient,
        "coach_assistance": [],
        "next_actions": [
            {
                "action": "Practice explaining expected and worst-case lookup separately.",
                "evidence_ids": [str(evidence_id)],
                "breakpoint_ids": [],
                "based_on_insufficient_evidence": False,
            }
        ],
    }


def test_report_output_contract_requires_explicit_material_support_mode() -> None:
    fixture = _fixture("strong-independent-solution")
    value = fixture.report.model_dump(mode="json")
    del value["summary"][0]["based_on_insufficient_evidence"]

    with pytest.raises(ValueError):
        SessionReportSynthesis.model_validate(value)


def test_report_finding_rejects_ambiguous_insufficient_evidence_shape() -> None:
    with pytest.raises(ValueError):
        ReportFinding(
            title="Ambiguous",
            finding="This cannot both cite evidence and claim insufficient evidence.",
            evidence_ids=[uuid4()],
            breakpoint_id=None,
            independence_level="INDEPENDENT",
            based_on_insufficient_evidence=True,
        )

    with pytest.raises(ValueError):
        ReportFinding(
            title="Missing attribution",
            finding="A supported result must retain how it was demonstrated.",
            evidence_ids=[uuid4()],
            breakpoint_id=None,
            independence_level=None,
            based_on_insufficient_evidence=False,
        )


def test_stage6b_logs_exclude_private_content_and_live_examiner_has_no_queue_import() -> None:
    runtime_files = (
        Path("app/outbox/dispatcher.py"),
        Path("app/outbox/consumer.py"),
        Path("app/reports/service.py"),
    )
    private_log_fields = (
        "actual_text",
        "input_content",
        "structured_report_json",
        "transcript",
        "source_code",
        "raw_output",
        "chain_of_thought",
    )
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        logging_lines = "\n".join(
            line for line in source.splitlines() if "logger." in line or "log." in line
        )
        assert not any(field in logging_lines for field in private_log_fields)

    examiner_source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("app/examiner").glob("*.py")
    )
    assert "app.outbox" not in examiner_source
    assert "app.worker" not in examiner_source
    assert "from rq" not in examiner_source

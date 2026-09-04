from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.countermap.models import CounterMapProjection
from app.countermap.projector import CounterMapProjector
from app.countermap.repository import CounterMapProjectionRepository
from app.countermap.routes import countermap_status
from app.countermap.service import (
    CounterMapGenerationError,
    CounterMapGenerationService,
    initial_countermap_generation_key,
)
from app.countermap.source import CounterMapSourceBuilder
from app.countermap.validator import CounterMapValidator
from app.db.session import build_engine
from app.evidence.coordinator import SessionEvaluationResult
from app.evidence.models import Evidence
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.models import OutboxEvent
from app.problems.models import Problem


class SuccessfulEvidenceCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        self.calls += 1
        return SessionEvaluationResult(interview_session_id=interview_session_id, units=())


class UnusedReportService:
    async def generate(self, **_: object) -> None:
        raise AssertionError("CounterMap generation must not invoke Session Report generation")


class BrokenProjector:
    def project(self, *_: object, **__: object) -> None:
        raise RuntimeError("injected deterministic projector failure")


async def test_completed_session_projects_valid_candidate_safe_empty_map(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session,
        initial_stage="IMPLEMENTATION",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage7a-source-projection",
    )
    bundle = await CounterMapSourceBuilder(db_session).build(development.interview_session.id)
    graph = CounterMapProjector().project(bundle)

    CounterMapValidator().validate(bundle=bundle, graph=graph)
    projection, created = await CounterMapProjectionRepository(db_session).prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key="stage7a-direct-projection",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    await CounterMapProjectionRepository(db_session).mark_ready(
        projection=projection,
        graph_json=graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )
    response = await countermap_status(development.interview_session.id, db_session)

    assert created is True
    assert response.status == "READY"
    assert response.graph is not None
    assert response.graph.semantic_identity() == graph.semantic_identity()
    assert response.graph.interview_session_id == development.interview_session.id


@pytest.mark.parametrize(
    "mismatch",
    ["source_identity", "source_watermark", "schema_version", "generation_policy_version"],
)
async def test_ready_generation_key_is_reused_only_for_exact_projection_identity(
    db_session: AsyncSession,
    mismatch: str,
) -> None:
    development = await create_development_interview(
        db_session,
        initial_stage="IMPLEMENTATION",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key=f"stage7a-identity-{mismatch}",
    )
    bundle = await CounterMapSourceBuilder(db_session).build(development.interview_session.id)
    graph = CounterMapProjector().project(bundle)
    repository = CounterMapProjectionRepository(db_session)
    projection, created = await repository.prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key=f"stage7a-same-key-{mismatch}",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    await repository.mark_ready(
        projection=projection,
        graph_json=graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )
    preserved_graph = projection.graph_json
    source_identity = bundle.source_identity
    source_watermark = bundle.source_watermark
    if mismatch == "source_identity":
        source_identity = "sha256:" + "f" * 64
    elif mismatch == "source_watermark":
        source_watermark += 1
    elif mismatch == "schema_version":
        projection.schema_version = "countermap.graph.legacy"
    else:
        projection.generation_policy_version = "countermap-projector.v1"
    await db_session.flush()

    same, should_generate = await repository.prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key=f"stage7a-same-key-{mismatch}",
        source_watermark=source_watermark,
        source_identity=source_identity,
    )

    assert same.id == projection.id
    assert should_generate is False
    assert same.status == "STALE"
    assert same.is_current is False
    assert same.graph_json == preserved_graph
    assert same.last_failure_category == "GENERATION_IDENTITY_MISMATCH"


async def test_new_generation_request_versions_atomically_supersede_ready_projection(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(
        db_session,
        initial_stage="IMPLEMENTATION",
    )
    await InterviewCompletionService(db_session).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage7a-versioned-regeneration",
    )
    bundle = await CounterMapSourceBuilder(db_session).build(development.interview_session.id)
    graph = CounterMapProjector().project(bundle)
    repository = CounterMapProjectionRepository(db_session)
    first, first_created = await repository.prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key="stage7a-generation-v1",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    await repository.mark_ready(
        projection=first,
        graph_json=graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )
    first.generation_policy_version = "countermap-projector.v1"
    await db_session.flush()
    assert await repository.current_ready(development.interview_session.id) is None
    first.generation_policy_version = graph.generation_policy_version
    await db_session.flush()
    reused, reused_created = await repository.prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key="stage7a-generation-v1",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    second, second_created = await repository.prepare_generation(
        session_id=development.interview_session.id,
        generation_request_key="stage7a-generation-v2",
        source_watermark=bundle.source_watermark,
        source_identity=bundle.source_identity,
    )
    await repository.mark_ready(
        projection=second,
        graph_json=graph.model_dump(mode="json"),
        generated_at=datetime.now(UTC),
    )

    assert first_created is True
    assert reused.id == first.id
    assert reused_created is False
    assert second_created is True
    assert second.projection_version == first.projection_version + 1
    assert first.status == "STALE"
    assert first.graph_json is not None
    assert second.status == "READY"
    assert second.is_current is True
    assert await repository.current_ready(development.interview_session.id) == second


async def test_outbox_fans_out_siblings_and_countermap_delivery_is_idempotent() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    session_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session,
                initial_stage="IMPLEMENTATION",
            )
            user_id = development.user.id
            problem_id = development.problem.id
            session_id = development.interview_session.id
            await InterviewCompletionService(session).complete(
                session_id=session_id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage7a-outbox-chain",
            )
            finalization = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == session_id,
                    OutboxEvent.event_type == "FINALIZE_SESSION_EVIDENCE",
                )
            )
            assert finalization is not None
            finalization.status = "PUBLISHED"
            finalization.attempt_count = 1
            finalization.published_at = datetime.now(UTC)
            finalization_id = finalization.id

        evidence = SuccessfulEvidenceCoordinator()
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=evidence,  # type: ignore[arg-type]
            report_service=UnusedReportService(),  # type: ignore[arg-type]
            countermap_service=CounterMapGenerationService(sessionmaker=sessions),
        )
        finalized = await consumer.consume(finalization_id, 1)

        async with sessions() as session, session.begin():
            siblings = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == session_id,
                        OutboxEvent.event_type.in_(
                            ("GENERATE_SESSION_REPORT", "GENERATE_COUNTERMAP")
                        ),
                    )
                    .order_by(OutboxEvent.event_type)
                )
            )
            assert {item.event_type for item in siblings} == {
                "GENERATE_SESSION_REPORT",
                "GENERATE_COUNTERMAP",
            }
            countermap_event = next(
                item for item in siblings if item.event_type == "GENERATE_COUNTERMAP"
            )
            report_event = next(
                item for item in siblings if item.event_type == "GENERATE_SESSION_REPORT"
            )
            assert countermap_event.payload["generation_request_key"] == (
                initial_countermap_generation_key(session_id)
            )
            assert report_event.status == "PENDING"
            countermap_event.status = "PUBLISHED"
            countermap_event.attempt_count = 1
            countermap_event.published_at = datetime.now(UTC)
            countermap_event_id = countermap_event.id

        generated = await consumer.consume(countermap_event_id, 1)
        duplicate = await consumer.consume(countermap_event_id, 1)

        async with sessions() as session:
            projections = list(
                await session.scalars(
                    select(CounterMapProjection).where(
                        CounterMapProjection.interview_session_id == session_id
                    )
                )
            )
            report_status = await session.scalar(
                select(OutboxEvent.status).where(OutboxEvent.id == report_event.id)
            )
        assert finalized.status == "COMPLETED"
        assert generated.status == "COMPLETED"
        assert duplicate.status == "SKIPPED"
        assert evidence.calls == 1
        assert len(projections) == 1
        assert projections[0].status == "READY"
        assert report_status == "PENDING"
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()


async def test_countermap_failure_does_not_corrupt_evidence_report_or_ready_version() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    session_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            development = await create_development_interview(
                session,
                initial_stage="IMPLEMENTATION",
            )
            user_id = development.user.id
            problem_id = development.problem.id
            session_id = development.interview_session.id
            await InterviewCompletionService(session).complete(
                session_id=session_id,
                reason="USER_ENDED",
                expected_state_version=0,
                idempotency_key="stage7a-failure-isolation",
            )

        healthy = CounterMapGenerationService(sessionmaker=sessions)
        first = await healthy.generate(
            interview_session_id=session_id,
            generation_request_key="stage7a-known-good",
        )
        second = await healthy.generate(
            interview_session_id=session_id,
            generation_request_key="stage7a-known-good-v2",
        )
        assert second.projection_version == first.projection_version + 1
        assert second.semantic_identity == first.semantic_identity
        async with sessions() as session, session.begin():
            report_event = OutboxEvent(
                aggregate_type="InterviewSession",
                aggregate_id=session_id,
                interview_session_id=session_id,
                event_type="GENERATE_SESSION_REPORT",
                payload={"interview_session_id": str(session_id)},
                deduplication_key=f"stage7a-report-isolation:{session_id}",
                status="PENDING",
                available_at=datetime.now(UTC),
                source_watermark=0,
            )
            session.add(report_event)
            evidence_before = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                )
                or 0
            )

        failing = CounterMapGenerationService(
            sessionmaker=sessions,
            projector=BrokenProjector(),  # type: ignore[arg-type]
        )
        try:
            await failing.generate(
                interview_session_id=session_id,
                generation_request_key="stage7a-injected-failure",
            )
        except CounterMapGenerationError as exc:
            assert exc.category == "RuntimeError"
        else:
            raise AssertionError("Injected projector failure unexpectedly succeeded")

        async with sessions() as session:
            repository = CounterMapProjectionRepository(session)
            current = await repository.current_ready(session_id)
            previous = await session.get(CounterMapProjection, first.projection_id)
            failed = await repository.for_request(
                session_id=session_id,
                generation_request_key="stage7a-injected-failure",
            )
            evidence_after = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                )
                or 0
            )
            persisted_report_status = await session.scalar(
                select(OutboxEvent.status).where(OutboxEvent.id == report_event.id)
            )
        assert current is not None
        assert current.id == second.projection_id
        assert current.status == "READY"
        assert previous is not None
        assert previous.status == "STALE"
        assert previous.graph_json is not None
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.graph_json is None
        assert evidence_after == evidence_before
        assert persisted_report_status == "PENDING"
    finally:
        if user_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                if problem_id is not None:
                    await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()

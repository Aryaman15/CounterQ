from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_stage1_1a_persistence import Stage1PersistenceGraph, add_event, create_stage1_graph
from test_stage1_1b_causal_persistence import create_ai_context
from test_stage5a_canonical_evaluation import (
    EvidenceFixture,
    canonical_concept,
    evidence_fixture,
    skill_by_key,
    validate_evidence,
)

from app.auth.models import User
from app.config.settings import create_settings, get_settings
from app.db.ids import uuid7
from app.db.session import build_engine
from app.evidence.contracts import AssessmentSourceInput, CreateAssessmentCommand
from app.evidence.models import (
    Assessment,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSource,
)
from app.evidence.validation import EvidenceValidationService
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    MasteryTransition,
    MasteryTransitionEvidence,
    RetestAttempt,
    RetestAttemptEvidence,
    RetestRecommendation,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.mastery.policy import MASTERY_POLICY_VERSION, MasteryPolicyV1
from app.mastery.routes import development_user_mastery
from app.mastery.service import (
    MasteryRecalculationService,
    initial_mastery_recalculation_key,
)
from app.mastery.source import MasterySourceBuilder, _is_independent_self_correction
from app.mastery.target_level import MasteryTargetLevelResolver
from app.observation.models import InterviewEvent
from app.observation.repository import ObservationRepository
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.problems.models import Concept, Problem
from app.problems.repository import ProblemRepository

FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
OUTBOX_NOW = datetime(2030, 9, 6, 23, 0, tzinfo=UTC)


class _PolicyV2(MasteryPolicyV1):
    version = "mastery_policy_test_v2"


class _UnexpectedEvidenceCoordinator:
    async def evaluate(self, _interview_session_id: UUID) -> None:
        raise AssertionError("Mastery outbox work must not evaluate session Evidence")


class _UnexpectedReportService:
    async def generate(self, **_kwargs: object) -> None:
        raise AssertionError("Mastery outbox work must not generate a Session Report")


@dataclass(frozen=True, slots=True)
class CommittedMasteryFixture:
    primary_graph: Stage1PersistenceGraph
    user_id: UUID
    extra_user_ids: tuple[UUID, ...]
    problem_ids: tuple[UUID, ...]
    session_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]
    concept_id: UUID
    skill_id: UUID


def test_mastery_development_routes_are_blocked_in_production(tmp_path: Path) -> None:
    settings = create_settings(env_file=tmp_path / "missing.env")
    settings.app_env = "production"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        fixture_response = client.get("/api/mastery/development/fixtures")
        user_response = client.get(
            "/api/mastery/development/users/8a000000-0000-4000-8000-000000000001"
        )
    assert fixture_response.status_code == 403
    assert user_response.status_code == 403


def test_mastery_demo_is_backed_by_production_policy_fixtures(tmp_path: Path) -> None:
    settings = create_settings(env_file=tmp_path / "missing.env")
    settings.app_env = "development"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        response = client.get("/api/mastery/development/fixtures")
    assert response.status_code == 200
    payload = response.json()
    assert [item["fixture_id"] for item in payload] == [
        "cold-start",
        "one-independent-session",
        "mixed-coach-retest-due",
        "multi-context-strong",
        "strong-but-stale",
    ]
    rendered = repr(payload).lower()
    assert "percentage" not in rendered and "score" not in rendered
    multi = next(item for item in payload if item["fixture_id"] == "multi-context-strong")
    assert any(item["state"] == "STRONG" for item in multi["overview"]["technical_concepts"])


def test_self_correction_source_boundary_requires_canonical_before_and_after() -> None:
    before, after, third = uuid4(), uuid4(), uuid4()
    exact_pair = {before, after}
    assert _is_independent_self_correction(
        polarity="POSITIVE",
        independence="INDEPENDENT",
        prompt_id=None,
        structured_snapshot_ids=exact_pair,
    )
    assert _is_independent_self_correction(
        polarity="MIXED",
        independence="INDEPENDENT",
        prompt_id=None,
        structured_snapshot_ids=exact_pair,
    )
    assert not _is_independent_self_correction(
        polarity="NEGATIVE",
        independence="INDEPENDENT",
        prompt_id=None,
        structured_snapshot_ids=exact_pair,
    )
    assert not _is_independent_self_correction(
        polarity="POSITIVE",
        independence="AFTER_PROBE",
        prompt_id=None,
        structured_snapshot_ids=exact_pair,
    )
    assert not _is_independent_self_correction(
        polarity="POSITIVE",
        independence="INDEPENDENT",
        prompt_id=uuid4(),
        structured_snapshot_ids=exact_pair,
    )
    assert not _is_independent_self_correction(
        polarity="POSITIVE",
        independence="INDEPENDENT",
        prompt_id=None,
        structured_snapshot_ids={before, after, third},
    )


async def _additional_evidence_fixture(
    session: AsyncSession,
    *,
    primary_graph: Stage1PersistenceGraph,
) -> tuple[EvidenceFixture, UUID, UUID]:
    secondary = await create_stage1_graph(session)
    interviews = InterviewRepository(session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1_800,
        problem_source="CURATED",
    )
    started_at = datetime.now(UTC)
    interview = await interviews.add_session(
        user_id=primary_graph.user.id,
        configuration_id=configuration.id,
        problem_version_id=secondary.problem_version.id,
        interview_pack_version_id=secondary.pack_version.id,
        current_stage="SETUP",
        state_version=0,
        status="ACTIVE",
        started_at=started_at,
        deadline_at=started_at + timedelta(minutes=30),
    )
    budget = await interviews.add_budget(
        session_id=interview.id,
        max_duration_seconds=1_800,
        max_probes=5,
        max_deep_reasoning_calls=8,
        reserved_post_interview_deep_reasoning_calls=0,
        max_strong_reasoning_calls=1,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5000"),
        hard_monetary_budget=Decimal("5.0000"),
        realtime_reserved_budget=Decimal("1.2500"),
    )
    graph = replace(
        secondary,
        user=primary_graph.user,
        configuration=configuration,
        interview_session=interview,
        budget=budget,
    )
    event = await add_event(session, graph, server_sequence=1)
    ai = await create_ai_context(session, graph, purpose="ASSESSMENT")
    service = EvidenceValidationService(session)
    validation_policy = await service.ensure_validation_policy_version()
    assessment = await service.create_assessment(
        CreateAssessmentCommand(
            interview_session_id=interview.id,
            assessment_dimension="CORRECTNESS",
            polarity="POSITIVE",
            rationale="A second independent context supports the same exact concept.",
            confidence=Decimal("0.9100"),
            status="VALIDATED",
            ai_invocation_id=ai.invocation.id,
            ai_policy_version_id=ai.policy.id,
            sources=(AssessmentSourceInput(event.id, "PRIMARY", 1),),
        )
    )
    return (
        EvidenceFixture(
            graph=graph,
            event=event,
            assessment=assessment,
            concept=await canonical_concept(session),
            skill=await skill_by_key(session, "complexity_reasoning"),
            validation_policy_id=validation_policy.id,
        ),
        secondary.user.id,
        secondary.problem.id,
    )


async def _committed_fixture(
    sessions: async_sessionmaker[AsyncSession],
    *,
    evidence_count: int = 2,
    independence: str = "INDEPENDENT",
) -> CommittedMasteryFixture:
    async with sessions() as session, session.begin():
        primary_fixture = await evidence_fixture(session)
        fixtures = [primary_fixture]
        evidence_rows = []
        extra_user_ids: list[UUID] = []
        problem_ids = [primary_fixture.graph.problem.id]
        for index in range(evidence_count):
            if index == 0:
                fixture = primary_fixture
            else:
                fixture, extra_user_id, problem_id = await _additional_evidence_fixture(
                    session,
                    primary_graph=primary_fixture.graph,
                )
                fixtures.append(fixture)
                extra_user_ids.append(extra_user_id)
                problem_ids.append(problem_id)
            evidence = await validate_evidence(
                session,
                fixture,
                polarity="POSITIVE",
                strength="STRONG",
                finding="Independent canonical reasoning supports the exact target.",
            )
            evidence.independence_level = independence
            evidence_rows.append(evidence)
        return CommittedMasteryFixture(
            primary_graph=primary_fixture.graph,
            user_id=primary_fixture.graph.user.id,
            extra_user_ids=tuple(extra_user_ids),
            problem_ids=tuple(problem_ids),
            session_ids=tuple(item.graph.interview_session.id for item in fixtures),
            evidence_ids=tuple(item.id for item in evidence_rows),
            concept_id=fixtures[0].concept.id,
            skill_id=fixtures[0].skill.id,
        )


async def _cleanup(
    sessions: async_sessionmaker[AsyncSession], fixture: CommittedMasteryFixture
) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            delete(User).where(User.id.in_((fixture.user_id, *fixture.extra_user_ids)))
        )
        await session.execute(delete(Problem).where(Problem.id.in_(fixture.problem_ids)))


async def test_mastery_recalculation_converges_and_invalidation_rebuilds() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions)
    service = MasteryRecalculationService(
        sessionmaker=sessions,
        clock=lambda: FIXED_NOW,
    )
    try:
        first, repeated = await asyncio.gather(
            service.recalculate(user_id=fixture.user_id),
            service.recalculate(user_id=fixture.user_id),
        )
        assert first.concept_projection_count == repeated.concept_projection_count == 1
        assert first.skill_projection_count == repeated.skill_projection_count == 1

        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            skill = await session.scalar(
                select(SkillMastery).where(SkillMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "STRONG"
            assert skill is not None and skill.state == "DEVELOPING"
            assert concept.projection_version == 1
            assert skill.projection_version == 1
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 2
            assert await _count(session, SkillMasteryEvidence, fixture.user_id) == 2
            assert await _transition_count(session, fixture.user_id) == 2
            transition_evidence = list(
                (
                    await session.execute(
                        select(
                            MasteryTransition.target_type,
                            MasteryTransitionEvidence.evidence_id,
                        )
                        .join(
                            MasteryTransitionEvidence,
                            MasteryTransitionEvidence.mastery_transition_id
                            == MasteryTransition.id,
                        )
                        .where(MasteryTransition.user_id == fixture.user_id)
                    )
                ).tuples()
            )
            for target_type in ("CONCEPT", "SKILL"):
                assert {
                    evidence_id
                    for family, evidence_id in transition_evidence
                    if family == target_type
                } == set(fixture.evidence_ids)
            admitted = await MasterySourceBuilder(session).build(
                fixture.user_id,
                target_level="NEW_GRAD",
                admitted_only=True,
            )
            assert {len(item.facts.evidence) for item in admitted.targets} == {2}

        duplicate = await service.recalculate(
            user_id=fixture.user_id,
        )
        assert duplicate.transition_count == 0

        async with sessions() as session, session.begin():
            result = await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[1],
                evidence_id=fixture.evidence_ids[1],
                reason="Stage 8 invalidation rebuild test",
                invalidated_at=FIXED_NOW,
            )
            replay = await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[1],
                evidence_id=fixture.evidence_ids[1],
                reason="A replay cannot rewrite the original reason",
                invalidated_at=FIXED_NOW,
            )
            assert result.changed is True
            assert replay.changed is False
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == fixture.user_id,
                        OutboxEvent.event_type == "RECALCULATE_MASTERY",
                    )
                )
                == 1
            )

        async with sessions() as session:
            admitted = await MasterySourceBuilder(session).build(
                fixture.user_id,
                target_level="NEW_GRAD",
                admitted_only=True,
            )
            assert {len(item.facts.evidence) for item in admitted.targets} == {1}

        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "DEVELOPING"
            assert concept.projection_version == 2
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 1
            latest_transition = await session.scalar(
                select(MasteryTransition)
                .where(
                    MasteryTransition.user_id == fixture.user_id,
                    MasteryTransition.target_type == "CONCEPT",
                    MasteryTransition.from_state == "STRONG",
                    MasteryTransition.to_state == "DEVELOPING",
                )
                .order_by(MasteryTransition.created_at.desc(), MasteryTransition.id.desc())
                .limit(1)
            )
            assert latest_transition is not None
            assert set(
                await session.scalars(
                    select(MasteryTransitionEvidence.evidence_id).where(
                        MasteryTransitionEvidence.mastery_transition_id
                        == latest_transition.id
                    )
                )
            ) == set(fixture.evidence_ids)

        async with sessions() as session, session.begin():
            await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[0],
                evidence_id=fixture.evidence_ids[0],
                reason="All evidence invalidated",
                invalidated_at=FIXED_NOW,
            )
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            skill = await session.scalar(
                select(SkillMastery).where(SkillMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "UNTESTED"
            assert skill is not None and skill.state == "UNTESTED"
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 0
            assert await _count(session, SkillMasteryEvidence, fixture.user_id) == 0
            assert await _transition_count(session, fixture.user_id) == 5
            assert len(
                list(
                    await session.scalars(
                        select(MasteryTransition).where(
                            MasteryTransition.user_id == fixture.user_id
                        )
                    )
                )
            ) == len(
                set(
                    await session.scalars(
                        select(MasteryTransition.transition_key).where(
                            MasteryTransition.user_id == fixture.user_id
                        )
                    )
                )
            )
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_policy_version_rebuilds_associations_without_rewriting_evidence() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    try:
        original = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
        await original.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            evidence_before = await session.get(Evidence, fixture.evidence_ids[0])
            assert evidence_before is not None
            canonical_before = (
                evidence_before.polarity,
                evidence_before.strength,
                evidence_before.finding,
                evidence_before.validation_status,
            )

        updated = MasteryRecalculationService(
            sessionmaker=sessions,
            policy=_PolicyV2(),
            clock=lambda: FIXED_NOW,
        )
        result = await updated.recalculate(user_id=fixture.user_id)
        assert result.transition_count == 0
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            links = list(
                await session.scalars(
                    select(ConceptMasteryEvidence).where(
                        ConceptMasteryEvidence.user_id == fixture.user_id
                    )
                )
            )
            evidence_after = await session.get(Evidence, fixture.evidence_ids[0])
            assert concept is not None and concept.projection_version == 2
            assert concept.mastery_policy_version == _PolicyV2.version
            assert {item.admitted_policy_version for item in links} == {_PolicyV2.version}
            assert evidence_after is not None
            assert (
                evidence_after.polarity,
                evidence_after.strength,
                evidence_after.finding,
                evidence_after.validation_status,
            ) == canonical_before
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_obsolete_retest_recommendation_is_superseded() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(
        sessions,
        evidence_count=1,
        independence="DIRECTLY_TAUGHT",
    )
    service = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
    try:
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            pending = list(
                await session.scalars(
                    select(RetestRecommendation).where(
                        RetestRecommendation.user_id == fixture.user_id,
                        RetestRecommendation.status == "PENDING",
                    )
                )
            )
            assert len(pending) == 1
            assert pending[0].concept_id == fixture.concept_id
            assert pending[0].skill_dimension_id is None
            pending[0].status = "SCHEDULED"
            db_session_breakpoint = Breakpoint(
                user_id=fixture.user_id,
                concept_id=fixture.concept_id,
                skill_dimension_id=fixture.skill_id,
                breakpoint_key="scheduled_recommendation_replacement",
                first_detected_session_id=fixture.session_ids[0],
                first_detected_at=FIXED_NOW,
                severity="HIGH",
                status="OPEN",
                summary="A canonical gap changes the current retest rationale.",
            )
            session.add(db_session_breakpoint)
            await session.flush()
            session.add(
                BreakpointEvidence(
                    breakpoint_id=db_session_breakpoint.id,
                    evidence_id=fixture.evidence_ids[0],
                    relationship="CREATED",
                )
            )

        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            recommendations = list(
                await session.scalars(
                    select(RetestRecommendation).where(
                        RetestRecommendation.user_id == fixture.user_id
                    )
                )
            )
            assert len(recommendations) == 2
            assert {item.status for item in recommendations} == {"SUPERSEDED", "PENDING"}
            active = [
                item
                for item in recommendations
                if item.status in {"PENDING", "SCHEDULED"}
            ]
            assert len(active) == 1
            assert active[0].breakpoint_id == db_session_breakpoint.id
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_duplicate_mastery_outbox_delivery_is_idempotent() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    service = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
    try:
        async with sessions() as session, session.begin():
            key = initial_mastery_recalculation_key(fixture.user_id, fixture.session_ids[0])
            event, created = await OutboxRepository(session).enqueue(
                aggregate_type="User",
                aggregate_id=fixture.user_id,
                interview_session_id=fixture.session_ids[0],
                event_type="RECALCULATE_MASTERY",
                payload={
                    "user_id": str(fixture.user_id),
                    "source_interview_session_id": str(fixture.session_ids[0]),
                    "source_session_level": "NEW_GRAD",
                    "mastery_policy_version": MASTERY_POLICY_VERSION,
                },
                deduplication_key=key,
                available_at=OUTBOX_NOW,
                source_watermark=1,
            )
            assert created is True
            event.status = "PUBLISHED"
            event.attempt_count = 1
            event.published_at = OUTBOX_NOW
            event_id = event.id

        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=_UnexpectedEvidenceCoordinator(),  # type: ignore[arg-type]
            report_service=_UnexpectedReportService(),  # type: ignore[arg-type]
            mastery_service=service,
            clock=lambda: OUTBOX_NOW,
        )
        completed = await consumer.consume(event_id, 1)
        duplicate = await consumer.consume(event_id, 1)
        assert completed.status == "COMPLETED"
        assert duplicate.status == "SKIPPED"
        assert duplicate.category == "OUTBOX_OWNERSHIP_LOST"
        async with sessions() as session:
            loaded_event = await session.get(OutboxEvent, event_id)
            assert loaded_event is not None and loaded_event.status == "COMPLETED"
            assert await _transition_count(session, fixture.user_id) == 2
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_session_deletion_rebuild_has_no_ghost_evidence() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    service = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
    try:
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            await session.execute(
                delete(InterviewSession).where(InterviewSession.id == fixture.session_ids[0])
            )
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "UNTESTED"
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 0
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_source_builder_uses_stable_problem_and_grounded_contexts() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    try:
        async with sessions() as session:
            baseline = await MasterySourceBuilder(session).build(fixture.user_id)
            baseline_target = next(
                item
                for item in baseline.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            baseline_decision = MasteryPolicyV1().evaluate(
                baseline_target.facts, now=FIXED_NOW
            )
            assert baseline_decision.state == "STRONG"
            assert baseline_decision.distinct_problem_count == 2
            assert baseline_decision.distinct_context_count == 2

        async with sessions() as session, session.begin():
            problem = await session.get(Problem, fixture.problem_ids[0])
            second_session = await session.get(InterviewSession, fixture.session_ids[1])
            assert problem is not None and second_session is not None
            problems = ProblemRepository(session)
            second_version = await problems.add_problem_version(
                problem=problem,
                version=f"v2-{uuid7()}",
                title="Same stable problem, revised statement",
                statement="The same technical problem with editorial clarification.",
                content_hash=f"sha256:{uuid7()}",
                schema_version="problem.v1",
            )
            second_pack = await problems.add_interview_pack_version(
                problem_version=second_version,
                schema_version="interview-pack.v1",
                pack_json={"expected_approaches": ["sliding_window"], "invariants": []},
                review_status="REVIEWED",
                preparation_policy_key="manual_review",
            )
            second_session.problem_version_id = second_version.id
            second_session.interview_pack_version_id = second_pack.id
            second_session.current_stage = "TESTING_DEBUGGING"
            second_configuration = await session.get(
                InterviewConfiguration, second_session.interview_configuration_id
            )
            assert second_configuration is not None
            second_configuration.mode = "COACH"

        async with sessions() as session:
            replay = await MasterySourceBuilder(session).build(fixture.user_id)
            replay_target = next(
                item
                for item in replay.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            replay_decision = MasteryPolicyV1().evaluate(replay_target.facts, now=FIXED_NOW)
            assert len({item.problem_version_id for item in replay_target.facts.evidence}) == 2
            assert replay_decision.distinct_problem_count == 1
            assert replay_decision.distinct_context_count == 1
            assert replay_decision.state == "DEVELOPING"

        async with sessions() as session, session.begin():
            event = await session.scalar(
                select(InterviewEvent)
                .join(
                    EvidenceSource,
                    EvidenceSource.interview_event_id == InterviewEvent.id,
                )
                .where(EvidenceSource.evidence_id == fixture.evidence_ids[1])
            )
            assert event is not None
            event.event_type = "CODE_SNAPSHOT_CREATED"
            event.source = "NATIVE_EDITOR"
            event.payload = {"trigger": "EDITOR_CHANGE"}
            await ObservationRepository(session).add_code_snapshot(
                session_id=fixture.session_ids[1],
                version_number=1,
                language="cpp",
                source_code="int solve() { return 1; }",
                content_hash=f"sha256:{uuid7()}",
                created_from_event_id=event.id,
            )

        async with sessions() as session:
            grounded = await MasterySourceBuilder(session).build(fixture.user_id)
            grounded_target = next(
                item
                for item in grounded.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            grounded_decision = MasteryPolicyV1().evaluate(
                grounded_target.facts, now=FIXED_NOW
            )
            assert grounded_decision.distinct_problem_count == 1
            assert grounded_decision.distinct_context_count == 2
            assert grounded_decision.state == "STRONG"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


@pytest.mark.parametrize("event_type", ["TEST_COMPLETED", "COMPILE_COMPLETED", "RUN_CLICKED"])
async def test_execution_only_negative_cannot_create_weak(event_type: str) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    try:
        async with sessions() as session, session.begin():
            evidence = await session.get(Evidence, fixture.evidence_ids[0])
            event = await session.scalar(
                select(InterviewEvent)
                .join(
                    EvidenceSource,
                    EvidenceSource.interview_event_id == InterviewEvent.id,
                )
                .where(EvidenceSource.evidence_id == fixture.evidence_ids[0])
            )
            assert evidence is not None and event is not None
            evidence.polarity = "NEGATIVE"
            event.event_type = event_type
            event.source = "NATIVE_RUNNER"
        async with sessions() as session:
            bundle = await MasterySourceBuilder(session).build(fixture.user_id)
            target = next(
                item
                for item in bundle.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            fact = target.facts.evidence[0]
            assert fact.demonstrates_reasoning_or_application is False
            assert MasteryPolicyV1().evaluate(target.facts, now=FIXED_NOW).state == "EXPOSED"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


@pytest.mark.parametrize("event_type", ["TRANSCRIPT_FINALIZED", "CODE_SNAPSHOT_CREATED"])
async def test_reasoning_or_meaningful_implementation_negative_can_be_weak(
    event_type: str,
) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    try:
        async with sessions() as session, session.begin():
            evidence = await session.get(Evidence, fixture.evidence_ids[0])
            event = await session.scalar(
                select(InterviewEvent)
                .join(
                    EvidenceSource,
                    EvidenceSource.interview_event_id == InterviewEvent.id,
                )
                .where(EvidenceSource.evidence_id == fixture.evidence_ids[0])
            )
            assert evidence is not None and event is not None
            evidence.polarity = "NEGATIVE"
            event.event_type = event_type
            if event_type == "CODE_SNAPSHOT_CREATED":
                event.source = "NATIVE_EDITOR"
                event.payload = {"trigger": "EDITOR_CHANGE"}
                await ObservationRepository(session).add_code_snapshot(
                    session_id=fixture.session_ids[0],
                    version_number=1,
                    language="cpp",
                    source_code="int solve() { return 0; }",
                    content_hash=f"sha256:{uuid7()}",
                    created_from_event_id=event.id,
                )
        async with sessions() as session:
            bundle = await MasterySourceBuilder(session).build(fixture.user_id)
            target = next(
                item
                for item in bundle.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            assert target.facts.evidence[0].demonstrates_reasoning_or_application is True
            assert MasteryPolicyV1().evaluate(target.facts, now=FIXED_NOW).state == "WEAK"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_source_builder_recognizes_structured_mixed_independent_correction() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    try:
        async with sessions() as session, session.begin():
            evidence = await session.get(Evidence, fixture.evidence_ids[0])
            assert evidence is not None
            assessment = await session.get(Assessment, evidence.originating_assessment_id)
            assert assessment is not None
            evidence.polarity = "MIXED"
            interactions = InterviewInteractionRepository(session)
            response = await interactions.add_response(
                interview_session_id=fixture.session_ids[0],
                started_at=FIXED_NOW,
                ended_at=FIXED_NOW + timedelta(seconds=10),
                completion_reason="SPONTANEOUS",
                summary="Structured before/after correction fixture.",
            )
            assessment.candidate_response_id = response.id
            for sequence in (2, 3):
                event = await add_event(
                    session,
                    fixture.primary_graph,
                    server_sequence=sequence,
                    event_type="CODE_SNAPSHOT_CREATED",
                    source="NATIVE_EDITOR",
                    now=FIXED_NOW + timedelta(seconds=sequence),
                )
                event.payload = {"trigger": "EDITOR_CHANGE"}
                await ObservationRepository(session).add_code_snapshot(
                    session_id=fixture.session_ids[0],
                    version_number=sequence,
                    language="cpp",
                    source_code=f"int solve() {{ return {sequence}; }}",
                    content_hash=f"sha256:{uuid7()}",
                    created_from_event_id=event.id,
                )
                await interactions.add_response_source(
                    interview_session_id=fixture.session_ids[0],
                    candidate_response_id=response.id,
                    interview_event_id=event.id,
                    source_role="CODE_CONTEXT",
                    sequence=sequence - 1,
                )
                session.add(
                    EvidenceSource(
                        evidence_id=evidence.id,
                        interview_event_id=event.id,
                        interview_session_id=fixture.session_ids[0],
                        source_role="CONTEXT",
                    )
                )

        async with sessions() as session:
            bundle = await MasterySourceBuilder(session).build(fixture.user_id)
            target = next(
                item
                for item in bundle.targets
                if item.family == "CONCEPT" and item.target_id == fixture.concept_id
            )
            fact = target.facts.evidence[0]
            assert fact.polarity == "MIXED"
            assert fact.is_self_correction is True
            assert MasteryPolicyV1().evaluate(target.facts, now=FIXED_NOW).state == "DEVELOPING"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_skill_family_uses_loaded_canonical_parent_metadata() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    concept_ids: tuple[UUID, ...] = ()
    try:
        async with sessions() as session, session.begin():
            suffix = str(uuid7()).replace("-", "")
            parent_a = Concept(
                canonical_key=f"family_a_{suffix}",
                display_name="Family A",
                category="ALGORITHMS",
                status="ACTIVE",
                description="Test family A.",
            )
            parent_b = Concept(
                canonical_key=f"family_b_{suffix}",
                display_name="Family B",
                category="ALGORITHMS",
                status="ACTIVE",
                description="Test family B.",
            )
            session.add_all([parent_a, parent_b])
            await session.flush()
            child_a = Concept(
                canonical_key=f"child_a_{suffix}",
                display_name="Child A",
                category="ALGORITHMS",
                parent_concept_id=parent_a.id,
                status="ACTIVE",
                description="Test child A.",
            )
            child_b = Concept(
                canonical_key=f"child_b_{suffix}",
                display_name="Child B",
                category="ALGORITHMS",
                parent_concept_id=parent_a.id,
                status="ACTIVE",
                description="Test child B.",
            )
            session.add_all([child_a, child_b])
            await session.flush()
            concept_ids = (child_a.id, child_b.id, parent_a.id, parent_b.id)
            links = list(
                await session.scalars(
                    select(EvidenceConcept)
                    .where(EvidenceConcept.evidence_id.in_(fixture.evidence_ids))
                    .order_by(EvidenceConcept.evidence_id)
                )
            )
            assert len(links) == 2
            links[0].concept_id = child_a.id
            links[1].concept_id = child_b.id

        async with sessions() as session, session.begin():
            same_family = await MasterySourceBuilder(session).build(fixture.user_id)
            skill = next(
                item
                for item in same_family.targets
                if item.family == "SKILL" and item.target_id == fixture.skill_id
            )
            assert {item.concept_family_key for item in skill.facts.evidence} == {
                f"family_a_{suffix}"
            }
            assert MasteryPolicyV1().evaluate(skill.facts, now=FIXED_NOW).state == "DEVELOPING"
            loaded_child_b = await session.get(Concept, concept_ids[1])
            assert loaded_child_b is not None
            loaded_child_b.parent_concept_id = concept_ids[3]

        async with sessions() as session:
            different_families = await MasterySourceBuilder(session).build(fixture.user_id)
            skill = next(
                item
                for item in different_families.targets
                if item.family == "SKILL" and item.target_id == fixture.skill_id
            )
            assert {item.concept_family_key for item in skill.facts.evidence} == {
                f"family_a_{suffix}",
                f"family_b_{suffix}",
            }
            assert MasteryPolicyV1().evaluate(skill.facts, now=FIXED_NOW).state == "STRONG"
    finally:
        await _cleanup(sessions, fixture)
        if concept_ids:
            async with sessions() as session, session.begin():
                await session.execute(delete(Concept).where(Concept.id.in_(concept_ids[:2])))
                await session.execute(delete(Concept).where(Concept.id.in_(concept_ids[2:])))
        await engine.dispose()


async def test_authoritative_level_converges_across_stale_jobs_and_old_invalidation() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    latest_session_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            completed_base = datetime(2027, 1, 1, tzinfo=UTC)
            for index, session_id in enumerate(fixture.session_ids):
                interview = await session.get(InterviewSession, session_id)
                assert interview is not None
                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                assert configuration is not None
                configuration.level = "INTERN"
                interview.status = "COMPLETED"
                interview.completed_at = completed_base + timedelta(days=index)

            interviews = InterviewRepository(session)
            latest_configuration = await interviews.add_configuration(
                mode="SIMULATION",
                level="NEW_GRAD",
                language="cpp",
                configured_duration_seconds=1_800,
                problem_source="CURATED",
            )
            latest = await interviews.add_session(
                user_id=fixture.user_id,
                configuration_id=latest_configuration.id,
                problem_version_id=fixture.primary_graph.problem_version.id,
                interview_pack_version_id=fixture.primary_graph.pack_version.id,
                current_stage="COMPLETED",
                state_version=1,
                status="COMPLETED",
                started_at=completed_base + timedelta(days=2),
                deadline_at=completed_base + timedelta(days=2, minutes=30),
            )
            latest.completed_at = completed_base + timedelta(days=2, minutes=25)
            latest_session_id = latest.id

        async with sessions() as session:
            assert (
                await MasteryTargetLevelResolver(session).resolve(fixture.user_id)
                == "NEW_GRAD"
            )

        service = MasteryRecalculationService(
            sessionmaker=sessions,
            clock=lambda: datetime(2032, 1, 1, tzinfo=UTC),
        )

        async def enqueue_published(
            source_session_id: UUID,
            source_level: str,
            suffix: str,
        ) -> UUID:
            async with sessions() as session, session.begin():
                event, created = await OutboxRepository(session).enqueue(
                    aggregate_type="User",
                    aggregate_id=fixture.user_id,
                    interview_session_id=source_session_id,
                    event_type="RECALCULATE_MASTERY",
                    payload={
                        "user_id": str(fixture.user_id),
                        "source_interview_session_id": str(source_session_id),
                        "source_session_level": source_level,
                        "mastery_policy_version": MASTERY_POLICY_VERSION,
                    },
                    deduplication_key=f"stale-level:{fixture.user_id}:{suffix}",
                    available_at=OUTBOX_NOW,
                    source_watermark=1,
                )
                assert created is True
                event.status = "PUBLISHED"
                event.attempt_count = 1
                event.published_at = OUTBOX_NOW
                return event.id

        assert latest_session_id is not None
        new_job = await enqueue_published(latest_session_id, "NEW_GRAD", "new-first")
        old_job = await enqueue_published(fixture.session_ids[0], "INTERN", "old-delayed")
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=_UnexpectedEvidenceCoordinator(),  # type: ignore[arg-type]
            report_service=_UnexpectedReportService(),  # type: ignore[arg-type]
            mastery_service=service,
            clock=lambda: OUTBOX_NOW,
        )
        assert (await consumer.consume(new_job, 1)).status == "COMPLETED"
        assert (await consumer.consume(old_job, 1)).status == "COMPLETED"

        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert concept is not None and concept.state == "DEVELOPING"
            assert (
                await MasterySourceBuilder(session).build(fixture.user_id)
            ).target_level == "NEW_GRAD"

        async with sessions() as session, session.begin():
            await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[0],
                evidence_id=fixture.evidence_ids[0],
                reason="Old INTERN evidence invalidation must retain NEW_GRAD authority",
                invalidated_at=datetime(2032, 1, 2, tzinfo=UTC),
            )
        after_invalidation = await service.recalculate(user_id=fixture.user_id)
        assert after_invalidation.target_level == "NEW_GRAD"

        reverse_old = await enqueue_published(
            fixture.session_ids[1], "INTERN", "reverse-old"
        )
        reverse_new = await enqueue_published(
            latest_session_id, "NEW_GRAD", "reverse-new"
        )
        first, second = await asyncio.gather(
            consumer.consume(reverse_old, 1),
            consumer.consume(reverse_new, 1),
        )
        assert {first.status, second.status} == {"COMPLETED"}
        async with sessions() as session:
            assert (
                await MasteryTargetLevelResolver(session).resolve(fixture.user_id)
                == "NEW_GRAD"
            )

        forward_clock = MasteryRecalculationService(
            sessionmaker=sessions,
            clock=lambda: datetime(2035, 1, 1, tzinfo=UTC),
        )
        backward_clock = MasteryRecalculationService(
            sessionmaker=sessions,
            clock=lambda: datetime(2034, 1, 1, tzinfo=UTC),
        )
        await forward_clock.recalculate(user_id=fixture.user_id)
        await backward_clock.recalculate(user_id=fixture.user_id)
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert concept is not None
            assert concept.last_evaluated_at == datetime(2035, 1, 1, tzinfo=UTC)
            assert concept.updated_at == datetime(2035, 1, 1, tzinfo=UTC)
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_target_level_resolver_has_deterministic_completion_tie_break() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    expected_level = "NEW_GRAD"
    try:
        async with sessions() as session, session.begin():
            completion = datetime(2028, 1, 1, tzinfo=UTC)
            levels = {
                min(fixture.session_ids, key=str): "INTERN",
                max(fixture.session_ids, key=str): "NEW_GRAD",
            }
            for session_id, level in levels.items():
                interview = await session.get(InterviewSession, session_id)
                assert interview is not None
                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                assert configuration is not None
                configuration.level = level
                interview.status = "COMPLETED"
                interview.completed_at = completion
            expected_level = levels[max(fixture.session_ids, key=str)]
        async with sessions() as session:
            assert (
                await MasteryTargetLevelResolver(session).resolve(fixture.user_id)
                == expected_level
            )
        development_result = await MasteryRecalculationService(
            sessionmaker=sessions,
            clock=lambda: FIXED_NOW,
        ).recalculate_for_development(
            user_id=fixture.user_id,
            target_level="INTERN",
        )
        assert development_result.target_level == "INTERN"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_persisted_candidate_read_never_shadow_grades_state(tmp_path: Path) -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    settings = create_settings(env_file=tmp_path / "missing.env")
    settings.app_env = "development"
    service = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
    try:
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            concept = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert concept is not None and concept.state == "STRONG"
            concept.state = "DEVELOPING"

        async with sessions() as session:
            response = await development_user_mastery(fixture.user_id, settings, session)
            concept_response = next(
                item for item in response.technical_concepts if item.target_id == fixture.concept_id
            )
            assert response.status == "STALE"
            assert concept_response.state == "DEVELOPING"
            assert concept_response.projection_version == 1
            persisted_states = {
                row.concept_id: row.state
                for row in await session.scalars(
                    select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
                )
            }
            assert concept_response.state == persisted_states[concept_response.target_id]

        async with sessions() as session, session.begin():
            concept_rows = list(
                await session.scalars(
                    select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
                )
            )
            skill_rows = list(
                await session.scalars(
                    select(SkillMastery).where(SkillMastery.user_id == fixture.user_id)
                )
            )
            for concept_row in concept_rows:
                concept_row.mastery_policy_version = "mastery_policy_v2"
            for skill_row in skill_rows:
                skill_row.mastery_policy_version = "mastery_policy_v2"

        async with sessions() as session:
            response = await development_user_mastery(fixture.user_id, settings, session)
            concept_response = next(
                item for item in response.technical_concepts if item.target_id == fixture.concept_id
            )
            assert response.mastery_policy_version == "mastery_policy_v2"
            assert response.status == "STALE"
            assert concept_response.state == "DEVELOPING"
            assert concept_response.mastery_policy_version == "mastery_policy_v2"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_repeated_mastery_transition_cycles_are_distinct_and_explainable() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    extra_user_id: UUID | None = None
    extra_problem_id: UUID | None = None
    service = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: FIXED_NOW)
    try:
        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[0],
                evidence_id=fixture.evidence_ids[0],
                reason="First correction-cycle invalidation",
                invalidated_at=FIXED_NOW,
            )
        await service.recalculate(user_id=fixture.user_id)

        async with sessions() as session, session.begin():
            second_fixture, extra_user_id, extra_problem_id = await _additional_evidence_fixture(
                session,
                primary_graph=fixture.primary_graph,
            )
            second_evidence = await validate_evidence(
                session,
                second_fixture,
                polarity="POSITIVE",
                strength="STRONG",
                finding="A later independent cycle demonstrates the target again.",
            )
            second_evidence_id = second_evidence.id
            second_session_id = second_fixture.graph.interview_session.id

        await service.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            await EvidenceValidationService(session).invalidate(
                interview_session_id=second_session_id,
                evidence_id=second_evidence_id,
                reason="Second correction-cycle invalidation",
                invalidated_at=FIXED_NOW,
            )
        await service.recalculate(user_id=fixture.user_id)
        duplicate = await service.recalculate(user_id=fixture.user_id)
        assert duplicate.transition_count == 0

        async with sessions() as session:
            downward = list(
                await session.scalars(
                    select(MasteryTransition)
                    .where(
                        MasteryTransition.user_id == fixture.user_id,
                        MasteryTransition.target_type == "CONCEPT",
                        MasteryTransition.from_state == "DEVELOPING",
                        MasteryTransition.to_state == "UNTESTED",
                    )
                    .order_by(MasteryTransition.created_at, MasteryTransition.id)
                )
            )
            assert len(downward) == 2
            assert len({item.transition_key for item in downward}) == 2
            linked = {
                transition.id: set(
                    await session.scalars(
                        select(MasteryTransitionEvidence.evidence_id).where(
                            MasteryTransitionEvidence.mastery_transition_id == transition.id
                        )
                    )
                )
                for transition in downward
            }
            assert {frozenset(ids) for ids in linked.values()} == {
                frozenset({fixture.evidence_ids[0]}),
                frozenset({second_evidence_id}),
            }
    finally:
        await _cleanup(sessions, fixture)
        if extra_user_id is not None and extra_problem_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == extra_user_id))
                await session.execute(delete(Problem).where(Problem.id == extra_problem_id))
        await engine.dispose()


async def test_mastery_database_constraints_are_enforced(db_session: AsyncSession) -> None:
    fixture = await evidence_fixture(db_session)
    evidence = await validate_evidence(db_session, fixture, polarity="POSITIVE")
    now = datetime.now(UTC)
    db_session.add(
        ConceptMastery(
            user_id=fixture.graph.user.id,
            concept_id=fixture.concept.id,
            state="DEVELOPING",
            last_evaluated_at=now,
            last_evidence_at=now,
            mastery_policy_version="constraint-test-v1",
            projection_version=1,
            supporting_evidence_count=1,
            context_diversity=1,
            updated_at=now,
        )
    )
    db_session.add(
        SkillMastery(
            user_id=fixture.graph.user.id,
            skill_dimension_id=fixture.skill.id,
            state="DEVELOPING",
            last_evaluated_at=now,
            last_evidence_at=now,
            mastery_policy_version="constraint-test-v1",
            projection_version=1,
            supporting_evidence_count=1,
            context_diversity=1,
            updated_at=now,
        )
    )
    await db_session.flush()

    duplicate_concept = ConceptMastery(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        state="EXPOSED",
        last_evaluated_at=now,
        mastery_policy_version="constraint-test-v1",
        projection_version=1,
        supporting_evidence_count=0,
        context_diversity=0,
        updated_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(duplicate_concept)
            await db_session.flush()

    duplicate_skill = SkillMastery(
        user_id=fixture.graph.user.id,
        skill_dimension_id=fixture.skill.id,
        state="EXPOSED",
        last_evaluated_at=now,
        mastery_policy_version="constraint-test-v1",
        projection_version=1,
        supporting_evidence_count=0,
        context_diversity=0,
        updated_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(duplicate_skill)
            await db_session.flush()

    invalid_association = ConceptMasteryEvidence(
        user_id=uuid4(),
        concept_id=fixture.concept.id,
        evidence_id=evidence.id,
        contribution_classification="SUPPORTING",
        context_key="constraint-context",
        admitted_policy_version="constraint-test-v1",
        admitted_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(invalid_association)
            await db_session.flush()

    invalid_transition = MasteryTransition(
        user_id=fixture.graph.user.id,
        target_type="CONCEPT",
        concept_id=None,
        skill_dimension_id=fixture.skill.id,
        from_state="UNTESTED",
        to_state="DEVELOPING",
        mastery_policy_version="constraint-test-v1",
        transition_key="sha256:" + "a" * 64,
        created_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(invalid_transition)
            await db_session.flush()

    recommendation = RetestRecommendation(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        recommended_after=now,
        priority=1,
        status="PENDING",
        strategy="DIFFERENT_CONTEXT",
        rationale="Retest the exact canonical concept.",
        generation_policy_version="constraint-test-v1",
        recommendation_key="constraint-valid:" + str(uuid7()),
        created_at=now,
        updated_at=now,
    )
    db_session.add(recommendation)
    await db_session.flush()
    attempt = RetestAttempt(
        retest_recommendation_id=recommendation.id,
        interview_session_id=fixture.graph.interview_session.id,
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.flush()
    db_session.add(RetestAttemptEvidence(retest_attempt_id=attempt.id, evidence_id=evidence.id))
    await db_session.flush()
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(RetestAttemptEvidence(retest_attempt_id=attempt.id, evidence_id=uuid4()))
            await db_session.flush()

    invalid_recommendation = RetestRecommendation(
        user_id=fixture.graph.user.id,
        recommended_after=now,
        priority=1,
        status="PENDING",
        strategy="DIFFERENT_CONTEXT",
        rationale="Constraint target must exist.",
        generation_policy_version="constraint-test-v1",
        recommendation_key="constraint-test:" + str(uuid7()),
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(invalid_recommendation)
            await db_session.flush()

    invalid_status = RetestRecommendation(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        recommended_after=now,
        priority=1,
        status="UNKNOWN",
        strategy="DIFFERENT_CONTEXT",
        rationale="Only the frozen recommendation workflow states are valid.",
        generation_policy_version="constraint-test-v1",
        recommendation_key="constraint-status:" + str(uuid7()),
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(invalid_status)
            await db_session.flush()

    negative_priority = RetestRecommendation(
        user_id=fixture.graph.user.id,
        concept_id=fixture.concept.id,
        recommended_after=now,
        priority=-1,
        status="PENDING",
        strategy="DIFFERENT_CONTEXT",
        rationale="Internal recommendation priority cannot be negative.",
        generation_policy_version="constraint-test-v1",
        recommendation_key="constraint-priority:" + str(uuid7()),
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(IntegrityError), db_session.no_autoflush:
        async with db_session.begin_nested():
            db_session.add(negative_priority)
            await db_session.flush()


async def _count(
    session: AsyncSession,
    model: type[ConceptMasteryEvidence] | type[SkillMasteryEvidence],
    user_id: UUID,
) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        or 0
    )


async def _transition_count(session: AsyncSession, user_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(MasteryTransition)
            .where(MasteryTransition.user_id == user_id)
        )
        or 0
    )

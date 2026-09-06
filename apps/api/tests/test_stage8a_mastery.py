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
from app.evidence.models import Evidence
from app.evidence.validation import EvidenceValidationService
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
from app.mastery.service import (
    MasteryRecalculationService,
    initial_mastery_recalculation_key,
)
from app.mastery.source import MasterySourceBuilder, _is_independent_self_correction
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.problems.models import Problem

FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
OUTBOX_NOW = datetime(2026, 9, 6, 23, 0, tzinfo=UTC)


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
            service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD"),
            service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD"),
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
            target_level="NEW_GRAD",
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

        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "DEVELOPING"
            assert concept.projection_version == 2
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 1

        async with sessions() as session, session.begin():
            await EvidenceValidationService(session).invalidate(
                interview_session_id=fixture.session_ids[0],
                evidence_id=fixture.evidence_ids[0],
                reason="All evidence invalidated",
                invalidated_at=FIXED_NOW,
            )
        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
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
        await original.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
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
        result = await updated.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
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
        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
        async with sessions() as session, session.begin():
            pending = list(
                await session.scalars(
                    select(RetestRecommendation).where(
                        RetestRecommendation.user_id == fixture.user_id,
                        RetestRecommendation.status == "PENDING",
                    )
                )
            )
            assert len(pending) == 2
            evidence = await session.get(Evidence, fixture.evidence_ids[0])
            assert evidence is not None
            evidence.independence_level = "INDEPENDENT"

        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
        async with sessions() as session:
            recommendations = list(
                await session.scalars(
                    select(RetestRecommendation).where(
                        RetestRecommendation.user_id == fixture.user_id
                    )
                )
            )
            assert len(recommendations) == 2
            assert {item.status for item in recommendations} == {"SUPERSEDED"}
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
                    "target_level": "NEW_GRAD",
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
        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
        async with sessions() as session, session.begin():
            from app.interviews.models import InterviewSession

            await session.execute(
                delete(InterviewSession).where(InterviewSession.id == fixture.session_ids[0])
            )
        await service.recalculate(user_id=fixture.user_id, target_level="NEW_GRAD")
        async with sessions() as session:
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == fixture.user_id)
            )
            assert concept is not None and concept.state == "UNTESTED"
            assert await _count(session, ConceptMasteryEvidence, fixture.user_id) == 0
    finally:
        await _cleanup(sessions, fixture)
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

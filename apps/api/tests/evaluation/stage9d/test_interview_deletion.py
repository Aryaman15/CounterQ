from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from test_stage1_1a_persistence import add_event, create_stage1_graph
from test_stage1_1b_causal_persistence import add_snapshot, add_transcript_segment
from test_stage5a_canonical_evaluation import evidence_fixture, validate_evidence
from test_stage8a_mastery import _cleanup, _committed_fixture

from app.ai_gateway.models import AIInvocation
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.principal import CurrentUser
from app.countermap.models import CounterMapProjection
from app.db.session import build_engine, get_session
from app.evidence.breakpoints import (
    MEANINGFUL_TECHNICAL_BOUNDARY,
    BreakpointCandidate,
    BreakpointService,
)
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
)
from app.evidence.units import AssessmentInputBuilder
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.repository import ExaminerRepository
from app.execution.models import ExecutionRun
from app.execution.models import TestResult as ExecutionTestResult
from app.execution.repository import ExecutionRepository
from app.interviews.completion import InterviewCompletionService
from app.interviews.deletion import (
    DELETION_INVALIDATION_REASON,
    InterviewDeletionCleanupService,
    InterviewDeletionRequestService,
)
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    SessionBudget,
)
from app.interviews.runtime import InterviewRuntime, SessionClosed
from app.main import create_app
from app.mastery.models import (
    ConceptMastery,
    RetestAttempt,
    RetestRecommendation,
    SkillMastery,
)
from app.mastery.service import MasteryRecalculationError, MasteryRecalculationService
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.observation.repository import ObservationRepository
from app.outbox.consumer import PostSessionOutboxConsumer
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.problems.models import Problem
from app.reports.models import SessionReport

NOW = datetime(2030, 9, 11, 2, 0, tzinfo=UTC)


def _app_for_user(user_id: UUID) -> tuple[FastAPI, AsyncEngine]:
    app = create_app()
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def isolated_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id,
        status="ACTIVE",
    )
    app.dependency_overrides[get_session] = isolated_session
    return app, engine


async def _publish_deletion(
    sessions: async_sessionmaker[AsyncSession], event_id: UUID
) -> None:
    async with sessions() as session, session.begin():
        event = await session.get(OutboxEvent, event_id)
        assert event is not None
        event.status = "PUBLISHED"
        event.attempt_count = 1
        event.published_at = NOW


async def _consume_deletion(
    sessions: async_sessionmaker[AsyncSession], event_id: UUID
) -> None:
    mastery = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: NOW)
    consumer = PostSessionOutboxConsumer(
        sessionmaker=sessions,
        evidence_coordinator=cast(Any, object()),
        report_service=cast(Any, object()),
        mastery_service=mastery,
        deletion_service=InterviewDeletionCleanupService(
            sessionmaker=sessions,
            mastery_service=mastery,
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )
    result = await consumer.consume(event_id, 1)
    assert result.status == "COMPLETED"


async def test_delete_api_is_owned_idempotent_and_immediately_unavailable() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    owner_app, owner_engine = _app_for_user(fixture.user_id)
    foreign_graph = None
    try:
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, fixture.session_ids[0])
            assert interview is not None
            interview.status = "COMPLETED"
            interview.current_stage = "COMPLETED"
            interview.completed_at = NOW
            foreign_graph = await create_stage1_graph(session)
            pending_report = SessionReport(
                interview_session_id=interview.id,
                report_version=1,
                generation_request_key=f"delete-report:{interview.id}",
                status="PENDING",
                validation_status="PENDING",
                source_watermark=interview.last_server_sequence,
                source_identity="sha256:" + "1" * 64,
                is_current=False,
            )
            pending_map = CounterMapProjection(
                interview_session_id=interview.id,
                projection_version=1,
                schema_version="countermap.v1",
                generation_policy_version="countermap.v1",
                generation_request_key=f"delete-countermap:{interview.id}",
                source_watermark=interview.last_server_sequence,
                source_identity="sha256:" + "2" * 64,
                status="BUILDING",
                is_current=False,
            )
            session.add_all((pending_report, pending_map))
            competing, _ = await OutboxRepository(session).enqueue(
                aggregate_type="InterviewSession",
                aggregate_id=interview.id,
                interview_session_id=interview.id,
                event_type="GENERATE_SESSION_REPORT",
                payload={"interview_session_id": str(interview.id)},
                deduplication_key=f"competing-delete-test:{interview.id}",
                available_at=NOW,
            )
            competing.status = "PROCESSING"
            competing.attempt_count = 1

        foreign_app, foreign_engine = _app_for_user(foreign_graph.user.id)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=foreign_app), base_url="http://test"
            ) as client:
                denied = await client.delete(f"/api/interviews/{fixture.session_ids[0]}")
            assert denied.status_code == 404
            async with sessions() as session:
                unchanged = await session.get(InterviewSession, fixture.session_ids[0])
                assert unchanged is not None and unchanged.status == "COMPLETED"
                assert await session.scalar(
                    select(func.count()).select_from(OutboxEvent).where(
                        OutboxEvent.interview_session_id == fixture.session_ids[0],
                        OutboxEvent.event_type == "DELETE_INTERVIEW",
                    )
                ) == 0

            async with AsyncClient(
                transport=ASGITransport(app=owner_app), base_url="http://test"
            ) as client:
                first = await client.delete(f"/api/interviews/{fixture.session_ids[0]}")
                repeated = await client.delete(f"/api/interviews/{fixture.session_ids[0]}")
                history = await client.get("/api/interviews")
                restore = await client.post(
                    f"/api/interviews/{fixture.session_ids[0]}/restore",
                    json={"client_instance_id": "stage9d-browser"},
                )
                report = await client.get(
                    f"/api/reports/sessions/{fixture.session_ids[0]}"
                )
                countermap = await client.get(
                    f"/api/countermap/sessions/{fixture.session_ids[0]}"
                )
        finally:
            await foreign_engine.dispose()

        assert first.status_code == repeated.status_code == 202
        assert first.json()["status"] == "DELETION_PENDING"
        assert first.json()["deletion_request_id"] == repeated.json()["deletion_request_id"]
        assert str(fixture.session_ids[0]) not in {
            row["interview_session_id"] for row in history.json()["items"]
        }
        assert restore.status_code == report.status_code == countermap.status_code == 404

        async with sessions() as session:
            interview = await session.get(InterviewSession, fixture.session_ids[0])
            assert interview is not None and interview.status == "DELETION_PENDING"
            deletion_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.interview_session_id == interview.id,
                    OutboxEvent.event_type == "DELETE_INTERVIEW",
                )
            )
            persisted_competing = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.deduplication_key
                    == f"competing-delete-test:{interview.id}"
                )
            )
            assert deletion_event is not None
            assert persisted_competing is not None
            assert (persisted_competing.status, persisted_competing.last_error) == (
                "FAILED",
                "SESSION_DELETION_PENDING",
            )
            with pytest.raises(SessionClosed):
                await InterviewRuntime(session).ensure_activity_allowed(interview.id)
            with pytest.raises(SessionClosed, match="pending deletion"):
                await InterviewCompletionService(session).complete(
                    session_id=interview.id,
                    reason="USER_ENDED",
                    expected_state_version=interview.state_version,
                    idempotency_key="stage9d-late-completion",
                )
            with pytest.raises(ValueError, match="pending deletion"):
                await AssessmentInputBuilder(session).build_for_revalidation(interview.id)
            deletion_event_id = deletion_event.id

        await _publish_deletion(sessions, deletion_event_id)
        await _consume_deletion(sessions, deletion_event_id)
        async with AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://test"
        ) as client:
            after_hard_delete = await client.delete(
                f"/api/interviews/{fixture.session_ids[0]}"
            )
        assert after_hard_delete.status_code == 404
    finally:
        await owner_engine.dispose()
        await _cleanup(sessions, fixture)
        if foreign_graph is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == foreign_graph.user.id))
                await session.execute(delete(Problem).where(Problem.id == foreign_graph.problem.id))
        await engine.dispose()


async def test_deletion_retracts_sole_evidence_repairs_breakpoint_and_hard_deletes_graph() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id: UUID | None = None
    problem_id: UUID | None = None
    session_id: UUID | None = None
    try:
        async with sessions() as session, session.begin():
            fixture = await evidence_fixture(session)
            evidence = await validate_evidence(session, fixture)
            interview = fixture.graph.interview_session
            transcript_event, transcript = await add_transcript_segment(
                session,
                fixture.graph,
                server_sequence=2,
                text="I think lookup is guaranteed constant time.",
            )
            _first_snapshot_event, first_snapshot = await add_snapshot(
                session,
                fixture.graph,
                server_sequence=3,
                version_number=1,
                source_code="int main() { return 0; }",
            )
            _second_snapshot_event, second_snapshot = await add_snapshot(
                session,
                fixture.graph,
                server_sequence=4,
                version_number=2,
                source_code="int main() { return 1; }",
                parent_snapshot_id=first_snapshot.id,
            )
            diff_event = await add_event(
                session,
                fixture.graph,
                server_sequence=5,
                event_type="MEANINGFUL_CODE_CHANGE",
                source="NATIVE_EDITOR",
            )
            code_diff = await ObservationRepository(session).add_code_diff(
                session_id=interview.id,
                from_snapshot_id=first_snapshot.id,
                to_snapshot_id=second_snapshot.id,
                diff_format="UNIFIED",
                diff_content="- return 0;\n+ return 1;",
                created_from_event_id=diff_event.id,
            )
            run_event = await add_event(
                session,
                fixture.graph,
                server_sequence=6,
                event_type="RUN_CLICKED",
                source="NATIVE_RUNNER",
            )
            execution = ExecutionRepository(session)
            run = await execution.add_run(
                session_id=interview.id,
                run_event_id=run_event.id,
                code_snapshot_id=second_snapshot.id,
                problem_version_id=fixture.graph.problem_version.id,
                language="cpp",
                started_at=NOW,
                execution_provider="stage9d-fake",
                idempotency_key="stage9d-run",
            )
            result = await execution.add_result(
                run_id=run.id,
                identifier="sample-1",
                input_json={"input": ""},
                expected_output="1",
                actual_output="1",
                status="PASSED",
                duration_ms=1,
                failure_classification=None,
            )
            examiner = ExaminerRepository(session)
            claim = await examiner.add_candidate_claim(
                interview_session_id=interview.id,
                origin_kind="TRANSCRIPT",
                source_transcript_segment_id=transcript.id,
                source_event_id=transcript_event.id,
                verbatim_excerpt="guaranteed constant time",
                normalized_claim="Hash lookup is guaranteed constant time",
                claim_type="COMPLEXITY",
                extraction_confidence=fixture.assessment.confidence,
                status="ACCEPTED_AS_INTERPRETATION",
                ai_invocation_id=fixture.assessment.ai_invocation_id,
                ai_policy_version_id=fixture.assessment.ai_policy_version_id,
            )
            decision = await examiner.add_examiner_decision(
                interview_session_id=interview.id,
                action="PROBE",
                target_claim_id=claim.id,
                target_event_id=transcript_event.id,
                target_code_snapshot_id=second_snapshot.id,
                proposed_probe_strategy="COUNTEREXAMPLE",
                technical_rationale="Test the claimed worst-case boundary.",
                source_event_watermark=6,
                source_state_version=interview.state_version,
                status="AUTHORIZED",
                ai_invocation_id=fixture.assessment.ai_invocation_id,
                ai_policy_version_id=fixture.assessment.ai_policy_version_id,
            )
            _delivery_event, delivered_segment = await add_transcript_segment(
                session,
                fixture.graph,
                server_sequence=7,
                text="What input could make those lookups collide?",
                speaker="COUNTERQ",
            )
            interactions = InterviewInteractionRepository(session)
            prompt = await interactions.add_prompt(
                interview_session_id=interview.id,
                examiner_decision_id=decision.id,
                origin="EXAMINER_DECISION",
                kind="PROBE",
                probe_strategy="COUNTEREXAMPLE",
                target_claim_id=claim.id,
                target_event_id=transcript_event.id,
                source_code_snapshot_id=second_snapshot.id,
                intent="Ask for an adversarial collision example.",
                status="DELIVERED",
                authorized_at=NOW,
            )
            delivery = await interactions.add_delivery(
                interview_session_id=interview.id,
                interviewer_prompt_id=prompt.id,
                delivery_attempt=1,
                intended_text="What input could make those lookups collide?",
                actual_transcript_segment_id=delivered_segment.id,
                delivery_state="DELIVERED",
                started_at=NOW,
                completed_at=NOW,
                ai_invocation_id=fixture.assessment.ai_invocation_id,
            )
            response = await interactions.add_response(
                interview_session_id=interview.id,
                interviewer_prompt_id=prompt.id,
                started_at=NOW,
                ended_at=NOW,
                completion_reason="COMPLETE",
                summary="Candidate considered collisions.",
            )
            await interactions.add_response_source(
                interview_session_id=interview.id,
                candidate_response_id=response.id,
                interview_event_id=transcript_event.id,
                source_role="PRIMARY",
                sequence=1,
            )
            interview.status = "COMPLETED"
            interview.current_stage = "COMPLETED"
            interview.completed_at = NOW
            interview.last_server_sequence = 7
            breakpoint = await BreakpointService(session).create_or_reinforce(
                BreakpointCandidate(
                    user_id=fixture.graph.user.id,
                    interview_session_id=interview.id,
                    concept_id=fixture.concept.id,
                    skill_dimension_id=fixture.skill.id,
                    assessment_dimension="CORRECTNESS",
                    evidence_ids=(evidence.id,),
                    boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
                    summary="Candidate treats average-case lookup as a guarantee.",
                    severity="HIGH",
                    known_subtype="worst_case_complexity",
                )
            )
            assert breakpoint.breakpoint_id is not None
            session.add_all(
                (
                    SessionReport(
                        interview_session_id=interview.id,
                        report_version=1,
                        generation_request_key=f"stage9d-report:{interview.id}",
                        status="PENDING",
                        validation_status="PENDING",
                        source_watermark=interview.last_server_sequence,
                        source_identity="sha256:" + "3" * 64,
                        is_current=False,
                    ),
                    CounterMapProjection(
                        interview_session_id=interview.id,
                        projection_version=1,
                        schema_version="countermap.v1",
                        generation_policy_version="countermap.v1",
                        generation_request_key=f"stage9d-map:{interview.id}",
                        source_watermark=interview.last_server_sequence,
                        source_identity="sha256:" + "4" * 64,
                        status="BUILDING",
                        is_current=False,
                    ),
                )
            )
            user_id = fixture.graph.user.id
            problem_id = fixture.graph.problem.id
            session_id = interview.id
            configuration_id = fixture.graph.configuration.id
            evidence_id = evidence.id
            assessment_id = fixture.assessment.id
            ai_invocation_id = fixture.assessment.ai_invocation_id
            breakpoint_id = breakpoint.breakpoint_id
            candidate_content_ids = {
                TranscriptSegment: transcript.id,
                CodeSnapshot: first_snapshot.id,
                CodeDiff: code_diff.id,
                ExecutionRun: run.id,
                ExecutionTestResult: result.id,
                CandidateClaim: claim.id,
                ExaminerDecision: decision.id,
                InterviewerPrompt: prompt.id,
                InterviewerPromptDelivery: delivery.id,
                CandidateResponse: response.id,
            }

        mastery = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: NOW)
        await mastery.recalculate(user_id=user_id)
        async with sessions() as session, session.begin():
            before_concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == user_id)
            )
            before_skill = await session.scalar(
                select(SkillMastery).where(SkillMastery.user_id == user_id)
            )
            assert before_concept is not None and before_concept.state == "WEAK"
            assert before_skill is not None and before_skill.state == "WEAK"
            recommendation = await session.scalar(
                select(RetestRecommendation).where(
                    RetestRecommendation.user_id == user_id,
                    RetestRecommendation.status == "PENDING",
                )
            )
            assert recommendation is not None
            recommendation.status = "SCHEDULED"
            deleted_attempt = RetestAttempt(
                retest_recommendation_id=recommendation.id,
                interview_session_id=session_id,
                started_at=NOW,
            )
            session.add(deleted_attempt)
            await session.flush()
            deleted_attempt_id = deleted_attempt.id
            deleted_recommendation_id = recommendation.id
        async with sessions() as session, session.begin():
            requested = await InterviewDeletionRequestService(
                session, clock=lambda: NOW
            ).request(
                principal_user_id=user_id,
                interview_session_id=session_id,
            )
        await _publish_deletion(sessions, requested.deletion_request_id)
        await _consume_deletion(sessions, requested.deletion_request_id)

        async with sessions() as session:
            assert await session.get(InterviewSession, session_id) is None
            assert await session.get(InterviewConfiguration, configuration_id) is None
            assert await session.get(SessionBudget, session_id) is None
            assert await session.get(Evidence, evidence_id) is None
            assert await session.get(Assessment, assessment_id) is None
            for model, row_id in candidate_content_ids.items():
                assert await session.get(model, row_id) is None
            assert await session.scalar(
                select(func.count()).select_from(EvidenceSource).where(
                    EvidenceSource.evidence_id == evidence_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(EvidenceConcept).where(
                    EvidenceConcept.evidence_id == evidence_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(EvidenceSkill).where(
                    EvidenceSkill.evidence_id == evidence_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(AssessmentSource).where(
                    AssessmentSource.assessment_id == assessment_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(CandidateResponseSource).where(
                    CandidateResponseSource.candidate_response_id == response.id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(InterviewEvent).where(
                    InterviewEvent.interview_session_id == session_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(SessionReport).where(
                    SessionReport.interview_session_id == session_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(CounterMapProjection).where(
                    CounterMapProjection.interview_session_id == session_id
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.interview_session_id == session_id
                )
            ) == 0

            invocation = await session.get(AIInvocation, ai_invocation_id)
            assert invocation is not None and invocation.interview_session_id is None
            repaired = await session.get(Breakpoint, breakpoint_id)
            assert repaired is not None
            assert repaired.first_detected_session_id is None
            assert (repaired.status, repaired.resolution_reason) == (
                "DISMISSED",
                DELETION_INVALIDATION_REASON,
            )
            assert await session.scalar(
                select(func.count()).select_from(BreakpointEvidence).where(
                    BreakpointEvidence.breakpoint_id == breakpoint_id
                )
            ) == 0
            concept = await session.scalar(
                select(ConceptMastery).where(ConceptMastery.user_id == user_id)
            )
            skill = await session.scalar(
                select(SkillMastery).where(SkillMastery.user_id == user_id)
            )
            assert concept is not None and concept.state == "UNTESTED"
            assert skill is not None and skill.state == "UNTESTED"
            assert not list(
                await session.scalars(
                    select(RetestRecommendation).where(
                        RetestRecommendation.user_id == user_id,
                        RetestRecommendation.status.in_(("PENDING", "SCHEDULED")),
                    )
                )
            )
            assert await session.get(RetestAttempt, deleted_attempt_id) is None
            superseded = await session.get(
                RetestRecommendation, deleted_recommendation_id
            )
            assert superseded is not None and superseded.status == "SUPERSEDED"
    finally:
        if user_id is not None and problem_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(User).where(User.id == user_id))
                await session.execute(delete(Problem).where(Problem.id == problem_id))
        await engine.dispose()


async def test_deleting_one_session_preserves_other_session_evidence_and_rebuilds_mastery() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    mastery = MasteryRecalculationService(sessionmaker=sessions, clock=lambda: NOW)
    try:
        await mastery.recalculate(user_id=fixture.user_id)
        async with sessions() as session, session.begin():
            before = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert before is not None and before.state == "STRONG"
            retest_interview = await session.get(
                InterviewSession, fixture.session_ids[1]
            )
            assert retest_interview is not None
            retest_interview.status = "COMPLETED"
            retest_interview.current_stage = "COMPLETED"
            retest_interview.completed_at = NOW
            recommendation = RetestRecommendation(
                user_id=fixture.user_id,
                concept_id=fixture.concept_id,
                skill_dimension_id=None,
                breakpoint_id=None,
                recommended_after=NOW,
                priority=85,
                status="SCHEDULED",
                strategy="DIFFERENT_CONTEXT",
                rationale="Verify the target in a surviving session.",
                generation_policy_version="mastery_policy_v1",
                recommendation_key=(
                    f"stage9d:INDEPENDENCE_NOT_VERIFIED:{fixture.user_id}"
                ),
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(recommendation)
            await session.flush()
            attempt = RetestAttempt(
                retest_recommendation_id=recommendation.id,
                interview_session_id=fixture.session_ids[1],
                started_at=NOW,
            )
            session.add(attempt)
            await session.flush()
            recommendation_id = recommendation.id
            attempt_id = attempt.id
        async with sessions() as session, session.begin():
            requested = await InterviewDeletionRequestService(
                session, clock=lambda: NOW
            ).request(
                principal_user_id=fixture.user_id,
                interview_session_id=fixture.session_ids[0],
            )
        await _publish_deletion(sessions, requested.deletion_request_id)
        await _consume_deletion(sessions, requested.deletion_request_id)

        async with sessions() as session:
            assert await session.get(InterviewSession, fixture.session_ids[0]) is None
            assert await session.get(Evidence, fixture.evidence_ids[0]) is None
            assert await session.get(InterviewSession, fixture.session_ids[1]) is not None
            assert await session.get(Evidence, fixture.evidence_ids[1]) is not None
            surviving_attempt = await session.get(RetestAttempt, attempt_id)
            assert surviving_attempt is not None
            assert surviving_attempt.outcome is not None
            surviving_recommendation = await session.get(
                RetestRecommendation, recommendation_id
            )
            assert surviving_recommendation is not None
            assert surviving_recommendation.status in {"ATTEMPTED", "SATISFIED"}
            concept = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert concept is not None
            assert concept.state != "STRONG"
            assert concept.supporting_evidence_count == 1
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_failed_cleanup_rolls_back_then_retries_without_half_repaired_state() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=1)
    try:
        async with sessions() as session, session.begin():
            requested = await InterviewDeletionRequestService(session).request(
                principal_user_id=fixture.user_id,
                interview_session_id=fixture.session_ids[0],
            )
        await _publish_deletion(sessions, requested.deletion_request_id)

        class FailingMastery:
            async def recalculate_in_transaction(self, *_args: object, **_kwargs: object) -> None:
                raise MasteryRecalculationError("TEST_FAILURE", "Injected cleanup failure")

        failed_cleanup = InterviewDeletionCleanupService(
            sessionmaker=sessions,
            mastery_service=cast(Any, FailingMastery()),
        )
        consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=cast(Any, object()),
            report_service=cast(Any, object()),
            deletion_service=failed_cleanup,
        )
        failed = await consumer.consume(requested.deletion_request_id, 1)
        assert (failed.status, failed.category) == ("RETRY", "TEST_FAILURE")

        async with sessions() as session:
            interview = await session.get(InterviewSession, fixture.session_ids[0])
            evidence = await session.get(Evidence, fixture.evidence_ids[0])
            event = await session.get(OutboxEvent, requested.deletion_request_id)
            assert interview is not None and interview.status == "DELETION_PENDING"
            assert evidence is not None and evidence.validation_status == "VALID"
            assert event is not None and event.status == "RETRY"

        async with sessions() as session, session.begin():
            event = await session.get(OutboxEvent, requested.deletion_request_id)
            assert event is not None
            event.status = "PUBLISHED"
            event.attempt_count = 2
            event.published_at = datetime.now(UTC)
            event.next_retry_at = None
        mastery = MasteryRecalculationService(sessionmaker=sessions)
        retry_consumer = PostSessionOutboxConsumer(
            sessionmaker=sessions,
            evidence_coordinator=cast(Any, object()),
            report_service=cast(Any, object()),
            mastery_service=mastery,
            deletion_service=InterviewDeletionCleanupService(
                sessionmaker=sessions,
                mastery_service=mastery,
            ),
        )
        retried = await retry_consumer.consume(requested.deletion_request_id, 2)
        assert retried.status == "COMPLETED"
        async with sessions() as session:
            assert await session.get(InterviewSession, fixture.session_ids[0]) is None
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()


async def test_breakpoint_survives_with_only_the_other_sessions_valid_support() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixture = await _committed_fixture(sessions, evidence_count=2)
    try:
        async with sessions() as session, session.begin():
            evidence_rows = list(
                await session.scalars(
                    select(Evidence).where(Evidence.id.in_(fixture.evidence_ids))
                )
            )
            for evidence in evidence_rows:
                evidence.polarity = "NEGATIVE"
                evidence.finding = "The same meaningful gap persisted in this context."
            created = await BreakpointService(session).create_or_reinforce(
                BreakpointCandidate(
                    user_id=fixture.user_id,
                    interview_session_id=fixture.session_ids[0],
                    concept_id=fixture.concept_id,
                    skill_dimension_id=fixture.skill_id,
                    assessment_dimension="CORRECTNESS",
                    evidence_ids=fixture.evidence_ids,
                    boundary_kind=MEANINGFUL_TECHNICAL_BOUNDARY,
                    summary="The candidate repeats the same correctness gap.",
                    severity="HIGH",
                )
            )
            assert created.breakpoint_id is not None
            breakpoint_id = created.breakpoint_id
            requested = await InterviewDeletionRequestService(session).request(
                principal_user_id=fixture.user_id,
                interview_session_id=fixture.session_ids[0],
            )
        await _publish_deletion(sessions, requested.deletion_request_id)
        await _consume_deletion(sessions, requested.deletion_request_id)

        async with sessions() as session:
            breakpoint = await session.get(Breakpoint, breakpoint_id)
            assert breakpoint is not None
            assert breakpoint.status == "OPEN"
            assert breakpoint.first_detected_session_id is None
            links = list(
                await session.scalars(
                    select(BreakpointEvidence).where(
                        BreakpointEvidence.breakpoint_id == breakpoint.id
                    )
                )
            )
            assert [item.evidence_id for item in links] == [fixture.evidence_ids[1]]
            assert await session.get(Evidence, fixture.evidence_ids[1]) is not None
            mastery = await session.scalar(
                select(ConceptMastery).where(
                    ConceptMastery.user_id == fixture.user_id,
                    ConceptMastery.concept_id == fixture.concept_id,
                )
            )
            assert mastery is not None and mastery.state != "UNTESTED"
    finally:
        await _cleanup(sessions, fixture)
        await engine.dispose()

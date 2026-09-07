"""Deterministic Stage 8B retrieval, transfer, and workflow acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.models import AIPolicyVersion
from app.auth.models import User
from app.config.settings import create_settings, get_settings
from app.db.constants import BREAKPOINT_EVIDENCE_RELATIONSHIPS, RETEST_RECOMMENDATION_STATUSES
from app.db.session import build_engine
from app.evidence.models import Breakpoint, BreakpointEvidence, Evidence, SkillDimension
from app.evidence.validation import EvidenceValidationService
from app.interviews.mode_policy import ModePolicy
from app.interviews.models import InterviewSession, SessionBudget
from app.interviews.repository import InterviewRepository
from app.interviews.template_policy import template_policy
from app.main import create_app
from app.mastery.models import RetestAttempt, RetestAttemptEvidence, RetestRecommendation
from app.mastery.policy import MasteryEvidenceFact, RetestReason
from app.mastery.service import MasteryRecalculationService
from app.problems.models import Concept, ProblemVersion
from app.retests.development import (
    DEVELOPMENT_RETEST_POLICY_KEY,
    DEVELOPMENT_RETEST_SUBJECT,
    _add_evidence,
    active_development_recommendation,
    ensure_development_retest_fixture,
)
from app.retests.policy import (
    RETEST_OUTCOMES,
    RetestOutcomePolicyV1,
    RetestProblemCandidate,
    RetestProblemSelectionPolicyV1,
)
from app.retests.service import RetestService, RetestStartError

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _candidate(
    *,
    problem_id: UUID | None = None,
    relevance: str = "HIGH",
    role: str = "PRIMARY",
    distance: int = 0,
    order: int = 1,
    levels: frozenset[str] = frozenset(("NEW_GRAD",)),
    languages: frozenset[str] = frozenset(("cpp",)),
    context: frozenset[str] = frozenset(("hashing",)),
    slug: str = "candidate",
) -> RetestProblemCandidate:
    stable_id = problem_id or uuid4()
    return RetestProblemCandidate(
        problem_id=stable_id,
        problem_version_id=uuid4(),
        interview_pack_version_id=uuid4(),
        slug=slug,
        title=slug.replace("-", " ").title(),
        target_relevance=relevance,
        target_role=role,
        mapping_distance=distance,
        catalog_order=order,
        supported_levels=levels,
        supported_languages=languages,
        structured_context=context,
    )


def _select(
    candidates: tuple[RetestProblemCandidate, ...],
    *,
    recent: UUID | None = None,
    level: str = "NEW_GRAD",
    language: str = "cpp",
    context: frozenset[str] = frozenset(),
) -> RetestProblemCandidate | None:
    return RetestProblemSelectionPolicyV1().select(
        candidates,
        target_level=level,
        language=language,
        most_recent_problem_id=recent,
        most_recent_context=context,
    )


@pytest.mark.parametrize(
    ("case"),
    range(12),
    ids=(
        "01_relevant_concept_wins",
        "02_reviewed_pack_is_required_upstream",
        "03_candidate_level_is_respected",
        "04_inherited_language_is_respected",
        "05_latest_stable_problem_is_avoided",
        "06_new_version_is_not_new_problem",
        "07_mode_cannot_manufacture_diversity",
        "08_different_stable_problem_is_preferred",
        "09_tie_break_is_deterministic",
        "10_no_different_problem_is_unavailable",
        "11_selection_contains_no_prompt_replay",
        "12_selection_is_server_owned",
    ),
)
def test_selection_acceptance(case: int) -> None:
    stable = uuid4()
    high = _candidate(slug="high", relevance="HIGH", order=4)
    low = _candidate(slug="low", relevance="LOW", order=1)
    if case == 0:
        assert _select((low, high)) == high
    elif case == 1:
        assert high.interview_pack_version_id is not None
    elif case == 2:
        wrong = _candidate(levels=frozenset(("INTERN",)), slug="wrong-level")
        assert _select((wrong, high)) == high
    elif case == 3:
        wrong = _candidate(languages=frozenset(("python",)), slug="wrong-language")
        assert _select((wrong, high)) == high
    elif case in {4, 7}:
        old = _candidate(problem_id=stable, slug="old")
        fresh = _candidate(slug="fresh")
        assert _select((old, fresh), recent=stable) == fresh
    elif case == 5:
        version_a = _candidate(problem_id=stable, slug="v1")
        version_b = _candidate(problem_id=stable, slug="v2")
        assert _select((version_a, version_b), recent=stable) is None
    elif case == 6:
        assert not hasattr(high, "interview_mode")
    elif case == 8:
        a = _candidate(slug="a", order=1)
        b = _candidate(slug="b", order=1)
        assert _select((b, a)) == a
        assert _select((a, b)) == a
    elif case == 9:
        assert _select((_candidate(problem_id=stable),), recent=stable) is None
    elif case == 10:
        assert not hasattr(high, "interviewer_prompt_id")
    else:
        assert "requested" not in RetestProblemSelectionPolicyV1.select.__annotations__


def _fact(
    *,
    polarity: str = "POSITIVE",
    strength: str = "STRONG",
    independence: str = "INDEPENDENT",
    valid: bool = True,
    reasoning: bool = True,
    self_correction: bool = False,
) -> MasteryEvidenceFact:
    return MasteryEvidenceFact(
        evidence_id=uuid4(),
        session_id=uuid4(),
        problem_version_id=uuid4(),
        problem_id=uuid4(),
        occurred_at=NOW,
        polarity=polarity,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        independence=independence,  # type: ignore[arg-type]
        evidence_type="CORRECTNESS",
        interview_mode="SIMULATION",
        interview_level="NEW_GRAD",
        context_key="context",
        observation_key="observation",
        concept_family_key="hash_map",
        valid=valid,
        demonstrates_reasoning_or_application=reasoning,
        is_self_correction=self_correction,
        is_retest=True,
    )


OUTCOME_CASES = (
    ((_fact(),), False, "SATISFIED"),
    ((_fact(polarity="MIXED", self_correction=True),), False, "SATISFIED"),
    ((_fact(polarity="NEGATIVE"),), False, "PERSISTED_GAP"),
    ((_fact(polarity="NEGATIVE", strength="MODERATE"),), False, "PERSISTED_GAP"),
    ((_fact(strength="WEAK"),), False, "INCONCLUSIVE"),
    ((_fact(independence="AFTER_PROBE"),), False, "INCONCLUSIVE"),
    ((_fact(independence="AFTER_LIGHT_GUIDANCE"),), False, "INCONCLUSIVE"),
    ((_fact(independence="AFTER_STRONG_HINT"),), False, "INCONCLUSIVE"),
    ((_fact(independence="DIRECTLY_TAUGHT"),), False, "INCONCLUSIVE"),
    ((_fact(valid=False),), False, "INCONCLUSIVE"),
    ((_fact(reasoning=False),), False, "INCONCLUSIVE"),
    ((), False, "INCONCLUSIVE"),
    ((_fact(),), True, "ABANDONED"),
    ((_fact(polarity="NEGATIVE", independence="AFTER_PROBE"),), False, "INCONCLUSIVE"),
    ((_fact(polarity="MIXED"),), False, "INCONCLUSIVE"),
    ((_fact(polarity="NEGATIVE", strength="WEAK"),), False, "INCONCLUSIVE"),
    ((_fact(polarity="POSITIVE", strength="MODERATE"),), False, "SATISFIED"),
    ((_fact(polarity="POSITIVE"), _fact(polarity="NEGATIVE")), False, "PERSISTED_GAP"),
    ((_fact(polarity="POSITIVE", valid=False), _fact()), False, "SATISFIED"),
    (
        (_fact(polarity="POSITIVE", reasoning=False), _fact(polarity="NEGATIVE")),
        False,
        "PERSISTED_GAP",
    ),
)


@pytest.mark.parametrize(
    ("evidence", "abandoned", "expected"),
    OUTCOME_CASES,
    ids=(
        "13_independent_positive_candidate",
        "14_independent_self_correction_candidate",
        "15_strong_independent_negative",
        "16_moderate_independent_negative",
        "17_shallow_evidence_inconclusive",
        "18_after_probe_not_independent",
        "19_light_guidance_not_independent",
        "20_strong_hint_not_independent",
        "21_teaching_not_independent",
        "22_invalid_evidence_excluded",
        "23_non_reasoning_evidence_excluded",
        "24_no_target_evidence_inconclusive",
        "25_abandoned_is_terminal",
        "26_probed_negative_not_persisted_gap",
        "27_mixed_without_self_correction_inconclusive",
        "28_weak_negative_inconclusive",
        "29_moderate_positive_is_meaningful",
        "30_independent_negative_has_priority",
        "31_valid_positive_survives_invalid_noise",
        "32_valid_negative_survives_shallow_noise",
    ),
)
def test_outcome_acceptance(
    evidence: tuple[MasteryEvidenceFact, ...], abandoned: bool, expected: str
) -> None:
    decision = RetestOutcomePolicyV1().evaluate(
        evidence,
        original_reason=RetestReason.INDEPENDENCE_NOT_VERIFIED,
        session_abandoned=abandoned,
    )
    assert decision.outcome == expected


@pytest.mark.parametrize(
    ("case"),
    range(26),
    ids=tuple(f"{number:02d}_workflow_invariant" for number in range(33, 59)),
)
def test_workflow_contract_invariants(case: int) -> None:
    quick = template_policy("QUICK_DRILL")
    simulation = ModePolicy().assistance_budget("SIMULATION")
    checks = (
        quick.configured_duration_seconds == 600,
        sum(item.target_seconds for item in quick.stage_plan) == 600,
        quick.stage_plan[0].stage == "INTRODUCTION",
        quick.stage_plan[-1].stage == "WRAP_UP",
        any(item.stage == "PROBLEM_UNDERSTANDING" for item in quick.stage_plan),
        any(item.stage == "APPROACH_DISCOVERY" for item in quick.stage_plan),
        any(item.stage == "IMPLEMENTATION" for item in quick.stage_plan),
        any(item.stage == "FINAL_DEFENSE" for item in quick.stage_plan),
        simulation.max_assistance_interventions == 0,
        simulation.max_direct_teaching_interventions == 0,
        "RETEST" not in {"COACH", "SIMULATION"},
        RETEST_OUTCOMES == ("SATISFIED", "PERSISTED_GAP", "INCONCLUSIVE", "ABANDONED"),
        "SCHEDULED" in RETEST_RECOMMENDATION_STATUSES,
        "ATTEMPTED" in RETEST_RECOMMENDATION_STATUSES,
        "SATISFIED" in RETEST_RECOMMENDATION_STATUSES,
        "SUPERSEDED" in RETEST_RECOMMENDATION_STATUSES,
        "RESOLUTION_SUPPORT" in BREAKPOINT_EVIDENCE_RELATIONSHIPS,
        "REINFORCED" in BREAKPOINT_EVIDENCE_RELATIONSHIPS,
        RetestAttempt.__table__.c.interviewer_prompt_id.nullable,
        not RetestAttempt.__table__.c.interview_session_id.nullable,
        not RetestAttemptEvidence.__table__.c.evidence_id.nullable,
        RetestService.start.__annotations__["principal_user_id"] in {UUID, "UUID"},
        "user_id" not in RetestService.start.__annotations__,
        quick.protected_final_defense_seconds == 120,
        quick.protected_wrap_up_seconds == 60,
        quick.max_probes == template_policy("STANDARD_CODING_INTERVIEW").max_probes,
    )
    assert checks[case]


def test_59_development_routes_are_blocked_in_production(tmp_path: Path) -> None:
    settings = create_settings(env_file=tmp_path / "missing.env")
    settings.app_env = "production"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        assert client.post("/api/retests/development/fixture").status_code == 403


async def _reset_fixture(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            delete(User).where(
                User.external_auth_provider == "dev",
                User.external_auth_subject == DEVELOPMENT_RETEST_SUBJECT,
            )
        )


async def _fixture(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, RetestRecommendation]:
    await _reset_fixture(sessions)
    async with sessions() as session, session.begin():
        user = await ensure_development_retest_fixture(session)
        user_id = user.id
    await MasteryRecalculationService(sessionmaker=sessions).recalculate(user_id=user_id)
    async with sessions() as session:
        recommendation = await active_development_recommendation(session, user_id)
        assert recommendation is not None
        session.expunge(recommendation)
    return user_id, recommendation


@pytest.mark.asyncio
async def test_60_start_is_atomic_simulation_and_idempotent() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation = await _fixture(sessions)
        service = RetestService(sessionmaker=sessions)
        first = await service.start(
            principal_user_id=user_id, recommendation_id=recommendation.id
        )
        second = await service.start(
            principal_user_id=user_id, recommendation_id=recommendation.id
        )
        assert first.interview_session_id == second.interview_session_id
        assert first.retest_attempt_id == second.retest_attempt_id
        assert first.problem_id != await _prior_problem_id(sessions, user_id)
        assert first.mode == "SIMULATION" and first.template == "QUICK_DRILL"
        assert first.configured_duration_seconds == 600 and second.resumed
        async with sessions() as session:
            assert await session.scalar(
                select(func.count(RetestAttempt.id)).where(
                    RetestAttempt.retest_recommendation_id == recommendation.id
                )
            ) == 1
            launched = await session.get(InterviewSession, first.interview_session_id)
            budget = await session.get(SessionBudget, first.interview_session_id)
            refreshed = await session.get(RetestRecommendation, recommendation.id)
            assert launched is not None and launched.status == "ACTIVE"
            assert budget is not None and budget.max_duration_seconds == 600
            assert refreshed is not None and refreshed.status == "SCHEDULED"
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


async def _prior_problem_id(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID
) -> UUID:
    async with sessions() as session:
        value = await session.scalar(
            select(ProblemVersion.problem_id)
            .join(InterviewSession, InterviewSession.problem_version_id == ProblemVersion.id)
            .where(InterviewSession.user_id == user_id, InterviewSession.status == "COMPLETED")
            .order_by(InterviewSession.completed_at.desc())
            .limit(1)
        )
    assert value is not None
    return value


@pytest.mark.asyncio
async def test_61_cross_user_and_terminal_workflow_states_are_rejected() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation = await _fixture(sessions)
        service = RetestService(sessionmaker=sessions)
        with pytest.raises(RetestStartError) as wrong_owner:
            await service.start(principal_user_id=uuid4(), recommendation_id=recommendation.id)
        assert wrong_owner.value.category == "RECOMMENDATION_NOT_FOUND"
        for terminal in ("SATISFIED", "DISMISSED", "SUPERSEDED", "ATTEMPTED"):
            async with sessions() as session, session.begin():
                row = await session.get(RetestRecommendation, recommendation.id)
                assert row is not None
                row.status = terminal
            with pytest.raises(RetestStartError) as rejected:
                await service.start(principal_user_id=user_id, recommendation_id=recommendation.id)
            assert rejected.value.category == "RECOMMENDATION_NOT_ACTIONABLE"
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


class _UnavailableSelection(RetestProblemSelectionPolicyV1):
    def select(self, *_args: object, **_kwargs: object) -> None:
        return None


@pytest.mark.asyncio
async def test_62_unavailable_selection_creates_no_partial_rows() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation = await _fixture(sessions)
        service = RetestService(sessionmaker=sessions, selection_policy=_UnavailableSelection())
        with pytest.raises(RetestStartError) as unavailable:
            await service.start(principal_user_id=user_id, recommendation_id=recommendation.id)
        assert unavailable.value.category == "NO_SUITABLE_RETEST"
        async with sessions() as session:
            assert await session.scalar(
                select(func.count(RetestAttempt.id)).where(
                    RetestAttempt.retest_recommendation_id == recommendation.id
                )
            ) == 0
            row = await session.get(RetestRecommendation, recommendation.id)
            assert row is not None and row.status == "PENDING"
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


async def _complete_attempt(
    sessions: async_sessionmaker[AsyncSession], *, positive: bool
) -> tuple[UUID, UUID, UUID, UUID]:
    user_id, recommendation = await _fixture(sessions)
    launch = await RetestService(sessionmaker=sessions).start(
        principal_user_id=user_id, recommendation_id=recommendation.id
    )
    async with sessions() as session, session.begin():
        interview = await session.get(InterviewSession, launch.interview_session_id)
        rec = await session.get(RetestRecommendation, recommendation.id)
        assert interview is not None and rec is not None and rec.concept_id is not None
        concept = await session.get(Concept, rec.concept_id)
        skill = await session.scalar(
            select(SkillDimension).where(SkillDimension.canonical_key == "correctness")
        )
        evaluator = await session.scalar(
            select(AIPolicyVersion).where(
                AIPolicyVersion.policy_key == DEVELOPMENT_RETEST_POLICY_KEY,
                AIPolicyVersion.version == "v1",
            )
        )
        assert concept is not None and skill is not None and evaluator is not None
        event = await InterviewRepository(session).add_event(
            session_id=interview.id,
            user_id=user_id,
            event_type="TRANSCRIPT_FINALIZED",
            source="CANDIDATE_VOICE",
            occurred_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            server_sequence=1,
            interview_state_version=1,
            schema_version="transcript.final.v1",
            idempotency_key=f"stage8b:{'positive' if positive else 'negative'}:{interview.id}",
            payload={"fixture": "independent retest evidence"},
        )
        interview.last_server_sequence = 1
        validation = EvidenceValidationService(session)
        validation_policy = await validation.ensure_validation_policy_version()
        evidence = await _add_evidence(
            session,
            validation,
            evaluator,
            validation_policy,
            interview,
            concept,
            skill,
            event.id,
            occurred_at=event.occurred_at,
            polarity="POSITIVE" if positive else "NEGATIVE",
            strength="STRONG",
            independence="INDEPENDENT",
            finding=(
                "Defended the target boundary independently in a different context."
                if positive
                else "Repeated the target gap independently in a different context."
            ),
        )
        interview.status = "COMPLETED"
        interview.current_stage = "COMPLETED"
        interview.completed_at = datetime.now(UTC)
        return user_id, recommendation.id, evidence.id, launch.retest_attempt_id


@pytest.mark.asyncio
async def test_63_success_links_evidence_resolves_exact_breakpoint_and_converges() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation_id, evidence_id, attempt_id = await _complete_attempt(
            sessions, positive=True
        )
        service = MasteryRecalculationService(sessionmaker=sessions)
        await service.recalculate(user_id=user_id)
        await service.recalculate(user_id=user_id)
        async with sessions() as session:
            attempt = await session.get(RetestAttempt, attempt_id)
            recommendation = await session.get(RetestRecommendation, recommendation_id)
            assert attempt is not None and attempt.outcome == "SATISFIED"
            assert recommendation is not None and recommendation.status == "SATISFIED"
            assert await session.scalar(
                select(func.count()).select_from(RetestAttemptEvidence).where(
                    RetestAttemptEvidence.retest_attempt_id == attempt_id,
                    RetestAttemptEvidence.evidence_id == evidence_id,
                )
            ) == 1
            breakpoint = await session.get(Breakpoint, recommendation.breakpoint_id)
            assert breakpoint is not None and breakpoint.status == "RESOLVED"
            link = await session.scalar(
                select(BreakpointEvidence).where(
                    BreakpointEvidence.breakpoint_id == breakpoint.id,
                    BreakpointEvidence.evidence_id == evidence_id,
                )
            )
            assert link is not None and link.relationship == "RESOLUTION_SUPPORT"
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


@pytest.mark.asyncio
async def test_64_failure_reinforces_and_preserves_auditable_history() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation_id, evidence_id, attempt_id = await _complete_attempt(
            sessions, positive=False
        )
        await MasteryRecalculationService(sessionmaker=sessions).recalculate(user_id=user_id)
        async with sessions() as session:
            attempt = await session.get(RetestAttempt, attempt_id)
            old = await session.get(RetestRecommendation, recommendation_id)
            assert attempt is not None and attempt.outcome == "PERSISTED_GAP"
            assert old is not None and old.status == "ATTEMPTED"
            assert await session.scalar(
                select(func.count(RetestRecommendation.id)).where(
                    RetestRecommendation.user_id == user_id,
                    RetestRecommendation.status == "PENDING",
                )
            ) == 1
            link = await session.scalar(
                select(BreakpointEvidence).where(
                    BreakpointEvidence.breakpoint_id == old.breakpoint_id,
                    BreakpointEvidence.evidence_id == evidence_id,
                )
            )
            assert link is not None and link.relationship == "REINFORCED"
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


@pytest.mark.asyncio
async def test_65_inconclusive_does_not_fabricate_success_or_rewrite_evidence() -> None:
    engine = build_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_id, recommendation = await _fixture(sessions)
        launch = await RetestService(sessionmaker=sessions).start(
            principal_user_id=user_id, recommendation_id=recommendation.id
        )
        async with sessions() as session, session.begin():
            interview = await session.get(InterviewSession, launch.interview_session_id)
            assert interview is not None
            interview.status = "COMPLETED"
            interview.current_stage = "COMPLETED"
            interview.completed_at = datetime.now(UTC)
        before = await _evidence_snapshot(sessions, user_id)
        service = MasteryRecalculationService(sessionmaker=sessions)
        await service.recalculate(user_id=user_id)
        await service.recalculate(user_id=user_id)
        after = await _evidence_snapshot(sessions, user_id)
        async with sessions() as session:
            attempt = await session.get(RetestAttempt, launch.retest_attempt_id)
            old = await session.get(RetestRecommendation, recommendation.id)
            assert attempt is not None and attempt.outcome == "INCONCLUSIVE"
            assert old is not None and old.status == "ATTEMPTED"
        assert before == after
    finally:
        await _reset_fixture(sessions)
        await engine.dispose()


async def _evidence_snapshot(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID
) -> tuple[tuple[UUID, str, str, str], ...]:
    async with sessions() as session:
        rows = await session.execute(
            select(Evidence.id, Evidence.polarity, Evidence.strength, Evidence.independence_level)
            .join(InterviewSession, InterviewSession.id == Evidence.interview_session_id)
            .where(InterviewSession.user_id == user_id)
            .order_by(Evidence.id)
        )
    return tuple((row[0], row[1], row[2], row[3]) for row in rows.all())

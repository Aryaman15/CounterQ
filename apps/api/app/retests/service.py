"""Atomic RetestRecommendation launch through the normal interview runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evidence.models import Breakpoint, Evidence, EvidenceConcept
from app.interviews.mode_policy import ModePolicy
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.repository import InterviewRepository
from app.interviews.template_policy import template_policy
from app.mastery.models import RetestAttempt, RetestRecommendation
from app.mastery.target_level import MasteryTargetLevelResolver
from app.problems.models import (
    Concept,
    Problem,
    ProblemConcept,
    ProblemVersion,
)
from app.problems.service import CuratedProblemError, CuratedProblemService
from app.retests.policy import RetestProblemCandidate, RetestProblemSelectionPolicyV1


class RetestStartError(RuntimeError):
    def __init__(self, category: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class RetestLaunch:
    recommendation_id: UUID
    retest_attempt_id: UUID
    interview_session_id: UUID
    problem_id: UUID
    problem_version_id: UUID
    problem_title: str
    language: str
    target_level: str
    mode: str
    template: str
    configured_duration_seconds: int
    resumed: bool


class RetestLanguageResolver:
    """Use the latest target-relevant completed configuration, then latest completed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, *, user_id: UUID, concept_id: UUID) -> str:
        relevant = await self._session.scalar(
            select(InterviewConfiguration.language)
            .join(
                InterviewSession,
                InterviewSession.interview_configuration_id == InterviewConfiguration.id,
            )
            .join(Evidence, Evidence.interview_session_id == InterviewSession.id)
            .join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == "COMPLETED",
                InterviewSession.completed_at.is_not(None),
                Evidence.validation_status == "VALID",
                Evidence.invalidated_at.is_(None),
                EvidenceConcept.concept_id == concept_id,
            )
            .order_by(
                InterviewSession.completed_at.desc(),
                Evidence.created_at.desc(),
                InterviewSession.id.desc(),
            )
            .limit(1)
        )
        if relevant is not None:
            return relevant
        latest = await self._session.scalar(
            select(InterviewConfiguration.language)
            .join(
                InterviewSession,
                InterviewSession.interview_configuration_id == InterviewConfiguration.id,
            )
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == "COMPLETED",
                InterviewSession.completed_at.is_not(None),
            )
            .order_by(InterviewSession.completed_at.desc(), InterviewSession.id.desc())
            .limit(1)
        )
        if latest is None:
            raise RetestStartError(
                "LANGUAGE_CONTEXT_UNAVAILABLE",
                "No suitable retest is available in this language yet.",
            )
        return latest


class RetestService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        selection_policy: RetestProblemSelectionPolicyV1 | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._selection_policy = selection_policy or RetestProblemSelectionPolicyV1()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def start(
        self,
        *,
        principal_user_id: UUID,
        recommendation_id: UUID,
    ) -> RetestLaunch:
        async with self._sessionmaker() as session, session.begin():
            recommendation = await session.scalar(
                select(RetestRecommendation)
                .where(RetestRecommendation.id == recommendation_id)
                .with_for_update()
            )
            if recommendation is None or recommendation.user_id != principal_user_id:
                raise RetestStartError("RECOMMENDATION_NOT_FOUND", "This retest is unavailable.")
            if recommendation.status == "SCHEDULED":
                return await self._existing_launch(session, recommendation)
            if recommendation.status != "PENDING":
                raise RetestStartError(
                    "RECOMMENDATION_NOT_ACTIONABLE", "This retest is no longer available."
                )
            now = self._clock()
            if now.tzinfo is None:
                raise RetestStartError("INVALID_CLOCK", "This retest is temporarily unavailable.")
            if recommendation.recommended_after > now:
                raise RetestStartError("RECOMMENDATION_NOT_READY", "This retest is not ready yet.")
            concept = await self._validate_target(session, recommendation)
            target_level = await MasteryTargetLevelResolver(session).resolve(principal_user_id)
            language = await RetestLanguageResolver(session).resolve(
                user_id=principal_user_id, concept_id=concept.id
            )
            recent_problem_id, recent_context = await _most_recent_target_context(
                session, user_id=principal_user_id, concept_id=concept.id
            )
            candidates = await _problem_candidates(session, concept)
            selected = self._selection_policy.select(
                candidates,
                target_level=target_level,
                language=language,
                most_recent_problem_id=recent_problem_id,
                most_recent_context=recent_context,
            )
            if selected is None:
                raise RetestStartError(
                    "NO_SUITABLE_RETEST",
                    "No suitable retest is available in this language yet.",
                )
            policy = template_policy("QUICK_DRILL")
            assert policy.configured_duration_seconds == 600
            interviews = InterviewRepository(session)
            configuration = await interviews.add_configuration(
                mode="SIMULATION",
                level=target_level,
                language=language,
                configured_duration_seconds=policy.configured_duration_seconds,
                problem_source="CURATED",
            )
            interview = await interviews.add_session(
                user_id=principal_user_id,
                configuration_id=configuration.id,
                problem_version_id=selected.problem_version_id,
                interview_pack_version_id=selected.interview_pack_version_id,
                current_stage="INTRODUCTION",
                state_version=0,
                status="ACTIVE",
                started_at=now,
                deadline_at=now + timedelta(seconds=policy.configured_duration_seconds),
            )
            assistance = ModePolicy().assistance_budget("SIMULATION")
            await interviews.add_budget(
                session_id=interview.id,
                max_duration_seconds=policy.configured_duration_seconds,
                max_probes=policy.max_probes,
                max_deep_reasoning_calls=policy.max_deep_reasoning_calls,
                reserved_post_interview_deep_reasoning_calls=(
                    policy.reserved_post_interview_deep_reasoning_calls
                ),
                max_strong_reasoning_calls=policy.max_strong_reasoning_calls,
                max_vision_calls=0,
                soft_monetary_budget=Decimal("2.5000"),
                hard_monetary_budget=Decimal("5.0000"),
                realtime_reserved_budget=Decimal("1.2500"),
                max_assistance_interventions=assistance.max_assistance_interventions,
                max_structural_hints=assistance.max_structural_hints,
                max_direct_teaching_interventions=assistance.max_direct_teaching_interventions,
                max_guided_retries=assistance.max_guided_retries,
            )
            attempt = RetestAttempt(
                retest_recommendation_id=recommendation.id,
                interview_session_id=interview.id,
                started_at=now,
            )
            session.add(attempt)
            recommendation.status = "SCHEDULED"
            recommendation.updated_at = now
            await session.flush()
            return _launch(
                recommendation,
                attempt,
                interview,
                selected,
                language=language,
                target_level=target_level,
                resumed=False,
            )

    async def _existing_launch(
        self, session: AsyncSession, recommendation: RetestRecommendation
    ) -> RetestLaunch:
        row = await session.execute(
            select(RetestAttempt, InterviewSession, ProblemVersion, Problem)
            .join(InterviewSession, InterviewSession.id == RetestAttempt.interview_session_id)
            .join(ProblemVersion, ProblemVersion.id == InterviewSession.problem_version_id)
            .join(Problem, Problem.id == ProblemVersion.problem_id)
            .where(RetestAttempt.retest_recommendation_id == recommendation.id)
            .order_by(RetestAttempt.started_at.desc(), RetestAttempt.id.desc())
            .limit(1)
        )
        found = row.one_or_none()
        if found is None:
            raise RetestStartError(
                "SCHEDULED_ATTEMPT_MISSING", "This retest is temporarily unavailable."
            )
        attempt, interview, version, problem = found
        if attempt.completed_at is not None or interview.status not in {
            "READY",
            "ACTIVE",
            "RECONNECTING",
        }:
            raise RetestStartError(
                "SCHEDULED_ATTEMPT_NOT_ACTIVE", "This retest is no longer available."
            )
        configuration = await session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        assert configuration is not None
        selected = RetestProblemCandidate(
            problem.id,
            version.id,
            interview.interview_pack_version_id,
            problem.slug or str(problem.id),
            version.title,
            "HIGH",
            "PRIMARY",
            0,
            int(version.io_schema_json.get("catalog_order", 0)),
            frozenset((configuration.level,)),
            frozenset((configuration.language,)),
            frozenset(),
        )
        return _launch(
            recommendation,
            attempt,
            interview,
            selected,
            language=configuration.language,
            target_level=configuration.level,
            resumed=True,
        )

    async def _validate_target(
        self, session: AsyncSession, recommendation: RetestRecommendation
    ) -> Concept:
        if recommendation.concept_id is None:
            raise RetestStartError(
                "CONCEPT_TARGET_REQUIRED", "No suitable retest is available yet."
            )
        concept = await session.get(Concept, recommendation.concept_id)
        if concept is None or concept.status != "ACTIVE":
            raise RetestStartError("TARGET_UNAVAILABLE", "This retest is no longer available.")
        if recommendation.breakpoint_id is not None:
            breakpoint = await session.get(Breakpoint, recommendation.breakpoint_id)
            if (
                breakpoint is None
                or breakpoint.user_id != recommendation.user_id
                or breakpoint.concept_id != concept.id
                or breakpoint.status not in {"OPEN", "RETEST_PENDING", "IMPROVING"}
            ):
                raise RetestStartError(
                    "BREAKPOINT_NOT_ACTIONABLE", "This retest is no longer available."
                )
        return concept


async def _problem_candidates(
    session: AsyncSession, target: Concept
) -> tuple[RetestProblemCandidate, ...]:
    catalog = await CuratedProblemService(session).list_candidate_catalog()
    result: list[RetestProblemCandidate] = []
    for version in catalog:
        mappings = list(
            await session.scalars(
                select(ProblemConcept).where(ProblemConcept.problem_version_id == version.id)
            )
        )
        eligible = [item for item in mappings if item.concept_id == target.id]
        if not eligible:
            continue
        best = min(
            eligible,
            key=lambda item: (
                -{"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(item.relevance, 0),
                -{"PRIMARY": 3, "SECONDARY": 2, "OPTIONAL": 1}.get(item.role, 0),
                str(item.concept_id),
            ),
        )
        try:
            pack = await CuratedProblemService(session).reviewed_pack_for_problem(version.id)
        except CuratedProblemError:
            continue
        languages = version.io_schema_json.get("languages")
        if not isinstance(languages, dict):
            continue
        context_ids = {item.concept_id for item in mappings if item.role == "PRIMARY"}
        context_keys = (
            set(
                await session.scalars(
                    select(Concept.canonical_key).where(Concept.id.in_(context_ids))
                )
            )
            if context_ids
            else set()
        )
        levels = _supported_levels(pack.pack_json)
        problem = version.problem
        raw_catalog_order = version.io_schema_json.get("catalog_order", 0)
        catalog_order = raw_catalog_order if isinstance(raw_catalog_order, int) else 0
        result.append(
            RetestProblemCandidate(
                problem_id=problem.id,
                problem_version_id=version.id,
                interview_pack_version_id=pack.id,
                slug=problem.slug or str(problem.id),
                title=version.title,
                target_relevance=best.relevance,
                target_role=best.role,
                mapping_distance=0,
                catalog_order=catalog_order,
                supported_levels=levels,
                supported_languages=frozenset(languages),
                structured_context=frozenset(context_keys),
            )
        )
    return tuple(result)


def _supported_levels(pack: dict[str, object]) -> frozenset[str]:
    rows = pack.get("level_considerations")
    if not isinstance(rows, list):
        return frozenset()
    return frozenset(
        str(row["level"])
        for row in rows
        if isinstance(row, dict) and row.get("level") in {"INTERN", "NEW_GRAD", "EARLY_CAREER"}
    )


async def _most_recent_target_context(
    session: AsyncSession, *, user_id: UUID, concept_id: UUID
) -> tuple[UUID | None, frozenset[str]]:
    attempted = await session.execute(
        select(Problem.id, ProblemVersion.id)
        .join(ProblemVersion, ProblemVersion.problem_id == Problem.id)
        .join(InterviewSession, InterviewSession.problem_version_id == ProblemVersion.id)
        .join(RetestAttempt, RetestAttempt.interview_session_id == InterviewSession.id)
        .join(
            RetestRecommendation,
            RetestRecommendation.id == RetestAttempt.retest_recommendation_id,
        )
        .where(
            RetestRecommendation.user_id == user_id,
            RetestRecommendation.concept_id == concept_id,
        )
        .order_by(RetestAttempt.started_at.desc(), RetestAttempt.id.desc())
        .limit(1)
    )
    attempted_context = attempted.one_or_none()
    if attempted_context is not None:
        problem_id, version_id = attempted_context
        return problem_id, await _structured_problem_context(session, version_id)
    row = await session.execute(
        select(Problem.id, ProblemVersion.id)
        .join(ProblemVersion, ProblemVersion.problem_id == Problem.id)
        .join(InterviewSession, InterviewSession.problem_version_id == ProblemVersion.id)
        .join(Evidence, Evidence.interview_session_id == InterviewSession.id)
        .join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id)
        .where(
            InterviewSession.user_id == user_id,
            Evidence.validation_status == "VALID",
            Evidence.invalidated_at.is_(None),
            EvidenceConcept.concept_id == concept_id,
        )
        .order_by(Evidence.created_at.desc(), Evidence.id.desc())
        .limit(1)
    )
    found = row.one_or_none()
    if found is None:
        return None, frozenset()
    problem_id, version_id = found
    return problem_id, await _structured_problem_context(session, version_id)


async def _structured_problem_context(
    session: AsyncSession, problem_version_id: UUID
) -> frozenset[str]:
    context = set(
        await session.scalars(
            select(Concept.canonical_key)
            .join(ProblemConcept, ProblemConcept.concept_id == Concept.id)
            .where(
                ProblemConcept.problem_version_id == problem_version_id,
                ProblemConcept.role == "PRIMARY",
            )
        )
    )
    return frozenset(context)


def _launch(
    recommendation: RetestRecommendation,
    attempt: RetestAttempt,
    interview: InterviewSession,
    selected: RetestProblemCandidate,
    *,
    language: str,
    target_level: str,
    resumed: bool,
) -> RetestLaunch:
    return RetestLaunch(
        recommendation_id=recommendation.id,
        retest_attempt_id=attempt.id,
        interview_session_id=interview.id,
        problem_id=selected.problem_id,
        problem_version_id=selected.problem_version_id,
        problem_title=selected.title,
        language=language,
        target_level=target_level,
        mode="SIMULATION",
        template="QUICK_DRILL",
        configured_duration_seconds=600,
        resumed=resumed,
    )

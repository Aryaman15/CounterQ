from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.repository import CandidateProfileRepository
from app.interviews.budget_policy import add_policy_session_budget
from app.interviews.models import InterviewConfiguration, InterviewSession, SessionBudget
from app.interviews.repository import InterviewRepository
from app.interviews.template_policy import (
    InterviewTemplate,
    InterviewTemplatePolicy,
    template_policy,
)
from app.problems.models import InterviewPackVersion, ProblemVersion
from app.problems.service import CuratedProblemService

SelfServeInterviewTemplate = Literal["QUICK_DRILL", "STANDARD_CODING_INTERVIEW"]
SELF_SERVE_INTERVIEW_TEMPLATES: tuple[SelfServeInterviewTemplate, ...] = (
    "QUICK_DRILL",
    "STANDARD_CODING_INTERVIEW",
)


class CandidateProfileRequired(ValueError):
    pass


class SelfServeInterviewSelectionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class SelfServeInterviewCreation:
    template: SelfServeInterviewTemplate
    problem_version: ProblemVersion
    pack_version: InterviewPackVersion
    configuration: InterviewConfiguration
    interview_session: InterviewSession
    budget: SessionBudget


class SelfServeInterviewCreationService:
    """Create one profile-backed, curated production interview atomically."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        user_id: UUID,
        problem_version_id: UUID,
        template: SelfServeInterviewTemplate,
        mode: Literal["COACH", "SIMULATION"],
        language: Literal["cpp", "python", "java"],
    ) -> SelfServeInterviewCreation:
        async with self._session.begin():
            profile = await CandidateProfileRepository(self._session).get(user_id)
            if profile is None:
                raise CandidateProfileRequired("CandidateProfile is required")
            policy = self._policy(template)
            curated = CuratedProblemService(self._session)
            problem_version = await curated.candidate_problem(problem_version_id)
            languages = problem_version.io_schema_json.get("languages")
            if not isinstance(languages, dict) or language not in languages:
                raise SelfServeInterviewSelectionInvalid(
                    "Requested language is not supported by the selected problem"
                )
            pack_version = await curated.reviewed_pack_for_problem(problem_version.id)
            if pack_version.problem_version_id != problem_version.id:
                raise SelfServeInterviewSelectionInvalid(
                    "Reviewed Interview Pack does not match the selected problem"
                )

            duration = policy.configured_duration_seconds
            if duration is None or not policy.stage_plan:
                raise SelfServeInterviewSelectionInvalid(
                    "Selected interview template is not available for self-serve launch"
                )
            started_at = self._clock()
            repository = InterviewRepository(self._session)
            configuration = await repository.add_configuration(
                mode=mode,
                level=profile.interview_level,
                language=language,
                configured_duration_seconds=duration,
                problem_source="CURATED",
            )
            interview = await repository.add_session(
                user_id=user_id,
                configuration_id=configuration.id,
                problem_version_id=problem_version.id,
                interview_pack_version_id=pack_version.id,
                current_stage=policy.stage_plan[0].stage,
                state_version=0,
                status="ACTIVE",
                started_at=started_at,
                deadline_at=started_at + timedelta(seconds=duration),
            )
            budget = await add_policy_session_budget(
                repository,
                session_id=interview.id,
                template=policy,
                mode=mode,
            )
        return SelfServeInterviewCreation(
            template=template,
            problem_version=problem_version,
            pack_version=pack_version,
            configuration=configuration,
            interview_session=interview,
            budget=budget,
        )

    @staticmethod
    def _policy(template: SelfServeInterviewTemplate) -> InterviewTemplatePolicy:
        if template not in SELF_SERVE_INTERVIEW_TEMPLATES:
            raise SelfServeInterviewSelectionInvalid(
                "Selected interview template is not available for self-serve launch"
            )
        return template_policy(cast(InterviewTemplate, template))

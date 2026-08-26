"""Typed server-only access to immutable Interview Pack content."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewSession
from app.problems.content import (
    Approach,
    CommonFollowup,
    CommonMisconception,
    FailureMode,
    InterviewPackContent,
    Invariant,
    ProbeOpportunity,
    canonical_hash,
)
from app.problems.models import InterviewPackVersion
from app.problems.service import CuratedProblemError, CuratedProblemService


class InterviewPackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_problem_version(self, problem_version_id: UUID) -> InterviewPackContent:
        pack = await CuratedProblemService(self._session).reviewed_pack_for_problem(
            problem_version_id
        )
        return self._validated(pack)

    async def for_session(self, session_id: UUID) -> InterviewPackContent:
        interview = await self._session.get(InterviewSession, session_id)
        if interview is None:
            raise CuratedProblemError("Interview session does not exist")
        pack = await self._session.get(InterviewPackVersion, interview.interview_pack_version_id)
        if pack is None:
            raise CuratedProblemError("Interview Pack is unavailable")
        return self._validated(pack)

    @staticmethod
    def approaches_by_concept(pack: InterviewPackContent, concept_key: str) -> list[Approach]:
        return [
            approach
            for approach in [*pack.expected_approaches, *pack.alternative_approaches]
            if concept_key in approach.concept_keys
        ]

    @staticmethod
    def approach_by_id(pack: InterviewPackContent, approach_id: str) -> Approach | None:
        return next(
            (
                approach
                for approach in [*pack.expected_approaches, *pack.alternative_approaches]
                if approach.approach_id == approach_id
            ),
            None,
        )

    @staticmethod
    def invariants_by_concept(pack: InterviewPackContent, concept_key: str) -> list[Invariant]:
        return [item for item in pack.invariants if concept_key in item.concept_keys]

    @staticmethod
    def misconceptions_by_concept(
        pack: InterviewPackContent,
        concept_key: str,
    ) -> list[CommonMisconception]:
        return [item for item in pack.common_misconceptions if concept_key in item.concept_keys]

    @staticmethod
    def failure_modes_by_concept(pack: InterviewPackContent, concept_key: str) -> list[FailureMode]:
        return [item for item in pack.failure_modes if concept_key in item.concept_keys]

    @staticmethod
    def probe_opportunities_by_concept(
        pack: InterviewPackContent,
        concept_key: str,
    ) -> list[ProbeOpportunity]:
        return [item for item in pack.probe_opportunities if concept_key in item.concept_keys]

    @staticmethod
    def common_followups_by_concept(
        pack: InterviewPackContent,
        concept_key: str,
    ) -> list[CommonFollowup]:
        return [item for item in pack.common_followups if concept_key in item.target_concepts]

    @staticmethod
    def common_followup_by_id(
        pack: InterviewPackContent,
        followup_id: str,
    ) -> CommonFollowup | None:
        return next((item for item in pack.common_followups if item.id == followup_id), None)

    @staticmethod
    def _validated(pack: InterviewPackVersion) -> InterviewPackContent:
        try:
            typed = InterviewPackContent.model_validate(pack.pack_json)
        except ValidationError as exc:
            raise CuratedProblemError("Persisted Interview Pack is invalid") from exc
        if (
            typed.schema_version != pack.schema_version
            or typed.version != pack.authored_version
            or typed.review_status != pack.review_status
            or canonical_hash(typed.model_dump(mode="json")) != pack.content_hash
        ):
            raise CuratedProblemError(
                "Persisted Interview Pack identity does not match its content"
            )
        return typed

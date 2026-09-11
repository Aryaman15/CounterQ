"""Server-trusted interview selection across curated and prepared custom content."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.ai_gateway.models import AIInvocation
from app.problems.content import CuratedContent, ProblemContent
from app.problems.custom import (
    CUSTOM_PREPARATION_POLICY_KEY,
    CUSTOM_PREPARATION_POLICY_VERSION,
    CUSTOM_QUALITY_GATE_VERSION,
    NORMALIZE_PURPOSE,
    PACK_PURPOSE,
    custom_problem_content_hash,
)
from app.problems.models import (
    Concept,
    CustomProblemPreparation,
    InterviewPackVersion,
    Problem,
    ProblemConcept,
    ProblemVersion,
)
from app.problems.pack_service import InterviewPackService
from app.problems.service import CuratedProblemService


class CandidateProblemSelectionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class CandidateProblemSelection:
    source_type: str
    problem_version: ProblemVersion
    pack_version: InterviewPackVersion
    custom_problem_preparation_id: UUID | None


class CandidateProblemSelectionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def select(
        self,
        *,
        user_id: UUID,
        problem_version_id: UUID,
        language: str,
    ) -> CandidateProblemSelection:
        version = await self._session.scalar(
            select(ProblemVersion)
            .join(Problem, Problem.id == ProblemVersion.problem_id)
            .options(joinedload(ProblemVersion.problem))
            .where(ProblemVersion.id == problem_version_id)
        )
        if version is None or version.problem.status != "ACTIVE":
            raise CandidateProblemSelectionInvalid("Selected problem is unavailable")
        languages = version.io_schema_json.get("languages")
        if not isinstance(languages, dict) or language not in languages:
            raise CandidateProblemSelectionInvalid("Selected language is unavailable")

        if version.problem.source_type == "CURATED":
            trusted = await CuratedProblemService(self._session).candidate_problem(version.id)
            pack = await CuratedProblemService(self._session).reviewed_pack_for_problem(trusted.id)
            InterviewPackService.validated_version(pack)
            return CandidateProblemSelection("CURATED", trusted, pack, None)

        if version.problem.source_type != "CUSTOM" or version.problem.owner_user_id != user_id:
            raise CandidateProblemSelectionInvalid("Selected problem is unavailable")
        preparation = await self._session.scalar(
            select(CustomProblemPreparation).where(
                CustomProblemPreparation.user_id == user_id,
                CustomProblemPreparation.operational_status == "COMPLETED",
                CustomProblemPreparation.quality_outcome == "READY",
                CustomProblemPreparation.prepared_problem_version_id == version.id,
                CustomProblemPreparation.preparation_policy_key == CUSTOM_PREPARATION_POLICY_KEY,
                CustomProblemPreparation.preparation_policy_version
                == CUSTOM_PREPARATION_POLICY_VERSION,
                CustomProblemPreparation.quality_gate_version == CUSTOM_QUALITY_GATE_VERSION,
            )
        )
        if preparation is None or preparation.prepared_pack_version_id is None:
            raise CandidateProblemSelectionInvalid("Custom problem has not passed preparation")
        custom_pack = await self._session.get(
            InterviewPackVersion, preparation.prepared_pack_version_id
        )
        if (
            custom_pack is None
            or custom_pack.problem_version_id != version.id
            or custom_pack.review_status != "REVIEWED"
            or custom_pack.preparation_policy_key != CUSTOM_PREPARATION_POLICY_KEY
            or custom_pack.ai_policy_version_id is None
        ):
            raise CandidateProblemSelectionInvalid("Prepared content binding is invalid")
        if not _sandbox_gate_passed(preparation.sandbox_validation_json):
            raise CandidateProblemSelectionInvalid("Prepared content trust evidence is invalid")
        if (
            preparation.normalization_ai_invocation_id is None
            or preparation.pack_ai_invocation_id is None
        ):
            raise CandidateProblemSelectionInvalid("Prepared content provenance is incomplete")
        normalization_invocation = await self._session.get(
            AIInvocation, preparation.normalization_ai_invocation_id
        )
        pack_invocation = await self._session.get(
            AIInvocation, preparation.pack_ai_invocation_id
        )
        if any(
            item is None
            or item.user_id != user_id
            or item.interview_session_id is not None
            or item.status != "SUCCEEDED"
            for item in (normalization_invocation, pack_invocation)
        ):
            raise CandidateProblemSelectionInvalid("Prepared content provenance is invalid")
        assert normalization_invocation is not None
        assert pack_invocation is not None
        if (
            normalization_invocation.purpose != NORMALIZE_PURPOSE
            or pack_invocation.purpose != PACK_PURPOSE
            or pack_invocation.ai_policy_version_id != custom_pack.ai_policy_version_id
        ):
            raise CandidateProblemSelectionInvalid("Prepared content policy provenance is invalid")

        typed_pack = InterviewPackService.validated_version(custom_pack)
        typed_problem = await self._validated_problem_version(version)
        try:
            CuratedContent(problem=typed_problem, interview_pack=typed_pack)
        except ValueError as exc:
            raise CandidateProblemSelectionInvalid(
                "Prepared problem and pack are not a valid content unit"
            ) from exc
        return CandidateProblemSelection("CUSTOM", version, custom_pack, preparation.id)

    async def _validated_problem_version(self, version: ProblemVersion) -> ProblemContent:
        mapping_rows = list(
            (
                await self._session.execute(
                    select(ProblemConcept, Concept)
                    .join(Concept, Concept.id == ProblemConcept.concept_id)
                    .where(ProblemConcept.problem_version_id == version.id)
                )
            ).all()
        )
        if not mapping_rows or any(concept.status != "ACTIVE" for _, concept in mapping_rows):
            raise CandidateProblemSelectionInvalid("Prepared problem concepts are unavailable")
        schema = version.io_schema_json
        try:
            typed = ProblemContent.model_validate(
                {
                    "schema_version": version.schema_version,
                    "slug": version.problem.slug,
                    "version": version.version,
                    "catalog_order": schema["catalog_order"],
                    "title": version.title,
                    "review_status": schema["review_status"],
                    "statement": version.statement,
                    "constraints": version.constraints_json["items"],
                    "examples": version.examples_json,
                    "execution": schema["execution"],
                    "languages": schema["languages"],
                    "problem_concepts": [
                        {
                            "canonical_key": concept.canonical_key,
                            "role": mapping.role,
                            "relevance": mapping.relevance,
                            "expected_importance": mapping.expected_importance,
                        }
                        for mapping, concept in mapping_rows
                    ],
                }
            )
        except (KeyError, ValueError) as exc:
            raise CandidateProblemSelectionInvalid(
                "Persisted custom problem content is invalid"
            ) from exc
        if custom_problem_content_hash(typed) != version.content_hash:
            raise CandidateProblemSelectionInvalid(
                "Persisted custom problem identity does not match its content"
            )
        return typed


def _sandbox_gate_passed(evidence: dict[str, object]) -> bool:
    languages = evidence.get("languages")
    if (
        evidence.get("gate_version") != CUSTOM_QUALITY_GATE_VERSION
        or not isinstance(evidence.get("provider"), str)
        or not isinstance(evidence.get("validation_case_hash"), str)
        or not isinstance(languages, dict)
        or set(languages) != {"cpp", "python", "java"}
    ):
        return False
    return all(
        isinstance(item, dict)
        and item.get("status") == "PASSED"
        and isinstance(item.get("runtime_version"), str)
        and isinstance(item.get("reference_hash"), str)
        and isinstance(item.get("case_count"), int)
        and item["case_count"] > 0
        for item in languages.values()
    )

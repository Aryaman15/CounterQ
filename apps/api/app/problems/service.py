"""Curated problem runtime boundary. Authoring files are imported, never served directly."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.problems.content import CuratedContent, canonical_hash, load_curated_content
from app.problems.models import Concept, InterviewPackVersion, Problem, ProblemConcept, ProblemVersion


class CuratedProblemError(ValueError):
    pass


class CuratedProblemService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seed(self, entries: list[CuratedContent] | None = None) -> int:
        seeded = 0
        for entry in entries or load_curated_content():
            await self._seed_one(entry)
            seeded += 1
        return seeded

    async def seed_concepts(self, concepts: list[dict[str, str]]) -> None:
        for item in concepts:
            existing = await self._session.scalar(
                select(Concept).where(Concept.canonical_key == item["canonical_key"])
            )
            if existing is None:
                self._session.add(
                    Concept(
                        canonical_key=item["canonical_key"],
                        display_name=item["display_name"],
                        category=item["category"],
                        status="ACTIVE",
                        description=item["description"],
                    )
                )

    async def _seed_one(self, entry: CuratedContent) -> None:
        problem_data = entry.problem.model_dump(mode="json")
        content_hash = canonical_hash(problem_data)
        problem = await self._session.scalar(select(Problem).where(Problem.source_type == "CURATED", Problem.slug == entry.problem.slug))
        if problem is None:
            problem = Problem(source_type="CURATED", slug=entry.problem.slug, status="ACTIVE")
            self._session.add(problem)
            await self._session.flush()
        version = await self._session.scalar(select(ProblemVersion).where(ProblemVersion.problem_id == problem.id, ProblemVersion.version == entry.problem.version))
        if version is not None:
            if version.content_hash != content_hash:
                raise CuratedProblemError(f"{entry.problem.slug}@{entry.problem.version} content changed; increment version")
        else:
            version = ProblemVersion(
                problem_id=problem.id,
                version=entry.problem.version,
                title=entry.problem.title,
                statement=entry.problem.statement,
                constraints_json={"items": entry.problem.constraints},
                examples_json=entry.problem.examples,
                io_schema_json={"catalog_order": entry.problem.catalog_order, "execution": entry.problem.execution.model_dump(mode="json"), "languages": {key: value.model_dump(mode="json") for key, value in entry.problem.languages.items()}},
                content_hash=content_hash,
                schema_version=entry.problem.schema_version,
            )
            self._session.add(version)
            await self._session.flush()
        pack_json = entry.interview_pack.model_dump(mode="json")
        existing_pack = await self._session.scalar(select(InterviewPackVersion).where(InterviewPackVersion.problem_version_id == version.id, InterviewPackVersion.schema_version == entry.interview_pack.schema_version))
        if existing_pack is None:
            self._session.add(InterviewPackVersion(problem_version_id=version.id, schema_version=entry.interview_pack.schema_version, preparation_policy_key=f"{entry.problem.slug}@{entry.interview_pack.version}", pack_json=pack_json, review_status=entry.interview_pack.review_status))
        elif canonical_hash(existing_pack.pack_json) != canonical_hash(pack_json):
            raise CuratedProblemError(f"{entry.problem.slug} Interview Pack changed; increment pack schema/version")
        for mapping in entry.problem.problem_concepts:
            concept = await self._session.scalar(select(Concept).where(Concept.canonical_key == mapping.canonical_key))
            if concept is None:
                raise CuratedProblemError(f"Unknown canonical concept {mapping.canonical_key}")
            existing_mapping = await self._session.scalar(select(ProblemConcept).where(ProblemConcept.problem_version_id == version.id, ProblemConcept.concept_id == concept.id))
            if existing_mapping is None:
                self._session.add(ProblemConcept(problem_version_id=version.id, concept_id=concept.id, relevance=mapping.relevance, expected_importance=mapping.expected_importance, role=mapping.role))

    async def list_candidate_catalog(self) -> list[ProblemVersion]:
        rows = await self._session.scalars(select(ProblemVersion).options(joinedload(ProblemVersion.problem)).join(Problem).where(Problem.source_type == "CURATED", Problem.status == "ACTIVE").order_by(ProblemVersion.io_schema_json["catalog_order"].as_integer()))
        return list(rows)

    async def candidate_problem(self, problem_version_id: UUID) -> ProblemVersion:
        version = await self._session.scalar(select(ProblemVersion).options(joinedload(ProblemVersion.problem)).join(Problem).where(ProblemVersion.id == problem_version_id, Problem.source_type == "CURATED", Problem.status == "ACTIVE"))
        if version is None:
            raise CuratedProblemError("Reviewed curated problem version is not selectable")
        return version

    async def reviewed_pack_for_problem(self, problem_version_id: UUID) -> InterviewPackVersion:
        pack = await self._session.scalar(select(InterviewPackVersion).where(InterviewPackVersion.problem_version_id == problem_version_id, InterviewPackVersion.review_status == "REVIEWED").order_by(InterviewPackVersion.created_at.desc()))
        if pack is None:
            raise CuratedProblemError("No reviewed Interview Pack is available")
        return pack

    async def server_pack_for_session(self, session_id: UUID) -> InterviewPackVersion:
        from app.interviews.models import InterviewSession
        interview = await self._session.get(InterviewSession, session_id)
        if interview is None:
            raise CuratedProblemError("Interview session does not exist")
        pack = await self._session.get(InterviewPackVersion, interview.interview_pack_version_id)
        if pack is None:
            raise CuratedProblemError("Interview Pack is unavailable")
        return pack

    async def pack_subset(self, session_id: UUID, *, concept_key: str | None = None, approach_id: str | None = None, followup_id: str | None = None) -> dict[str, object]:
        pack = await self.server_pack_for_session(session_id)
        payload = pack.pack_json
        if concept_key is None and approach_id is None and followup_id is None:
            return payload
        return {
            "expected_approaches": [item for item in payload.get("expected_approaches", []) if (approach_id is None or item.get("approach_id") == approach_id) and (concept_key is None or concept_key in item.get("concept_keys", []))],
            "common_followups": [item for item in payload.get("common_followups", []) if (followup_id is None or item.get("id") == followup_id) and (concept_key is None or concept_key in item.get("target_concepts", []))],
            "invariants": payload.get("invariants", []),
        }

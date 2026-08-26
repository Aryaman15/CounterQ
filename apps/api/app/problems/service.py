"""Immutable curated-content import and existing runtime problem queries."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.problems.content import ConceptOntology, CuratedContent, canonical_hash
from app.problems.models import (
    Concept,
    ConceptAlias,
    ConceptRelationship,
    InterviewPackVersion,
    Problem,
    ProblemConcept,
    ProblemVersion,
)


class CuratedProblemError(ValueError):
    pass


@dataclass
class SeedCounts:
    concepts: int = 0
    aliases: int = 0
    relationships: int = 0
    problems: int = 0
    problem_versions: int = 0
    interview_pack_versions: int = 0
    problem_concepts: int = 0

    def add(self, other: SeedCounts) -> None:
        for item in fields(self):
            setattr(self, item.name, getattr(self, item.name) + getattr(other, item.name))


class CuratedProblemService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seed_ontology(self, ontology: ConceptOntology) -> SeedCounts:
        counts = SeedCounts()
        concepts: dict[str, Concept] = {}
        created_concepts: set[str] = set()
        for authored_concept in ontology.concepts:
            existing = await self._session.scalar(
                select(Concept).where(Concept.canonical_key == authored_concept.canonical_key)
            )
            if existing is None:
                existing = Concept(
                    canonical_key=authored_concept.canonical_key,
                    display_name=authored_concept.display_name,
                    category=authored_concept.category,
                    status=authored_concept.status,
                    description=authored_concept.description,
                )
                self._session.add(existing)
                await self._session.flush()
                counts.concepts += 1
                created_concepts.add(authored_concept.canonical_key)
            concepts[authored_concept.canonical_key] = existing
        for authored_concept in ontology.concepts:
            row = concepts[authored_concept.canonical_key]
            parent = (
                concepts.get(authored_concept.parent_concept_key)
                if authored_concept.parent_concept_key
                else None
            )
            if authored_concept.canonical_key in created_concepts:
                row.parent_concept_id = parent.id if parent else None
            expected = (
                authored_concept.display_name,
                authored_concept.category,
                authored_concept.status,
                authored_concept.description,
                parent.id if parent else None,
            )
            actual = (
                row.display_name,
                row.category,
                row.status,
                row.description,
                row.parent_concept_id,
            )
            if actual != expected:
                raise CuratedProblemError(
                    f"Concept {authored_concept.canonical_key} conflicts with "
                    "immutable authored meaning"
                )
        await self._session.flush()
        for authored_alias in ontology.aliases:
            concept = concepts[authored_alias.concept_key]
            alias = await self._session.scalar(
                select(ConceptAlias).where(
                    ConceptAlias.normalized_alias == authored_alias.normalized_alias
                )
            )
            if alias is None:
                self._session.add(
                    ConceptAlias(
                        concept_id=concept.id,
                        alias=authored_alias.alias,
                        normalized_alias=authored_alias.normalized_alias,
                        alias_type=authored_alias.alias_type,
                    )
                )
                counts.aliases += 1
            elif (alias.concept_id, alias.alias, alias.alias_type) != (
                concept.id,
                authored_alias.alias,
                authored_alias.alias_type,
            ):
                raise CuratedProblemError(
                    f"Alias {authored_alias.normalized_alias!r} conflicts with "
                    "immutable authored meaning"
                )
        for authored_relationship in ontology.relationships:
            source = concepts[authored_relationship.from_concept_key]
            target = concepts[authored_relationship.to_concept_key]
            existing = await self._session.scalar(
                select(ConceptRelationship).where(
                    ConceptRelationship.from_concept_id == source.id,
                    ConceptRelationship.to_concept_id == target.id,
                    ConceptRelationship.relationship_type
                    == authored_relationship.relationship_type,
                )
            )
            if existing is None:
                self._session.add(
                    ConceptRelationship(
                        from_concept_id=source.id,
                        to_concept_id=target.id,
                        relationship_type=authored_relationship.relationship_type,
                    )
                )
                counts.relationships += 1
        await self._session.flush()
        return counts

    async def seed_problem(self, entry: CuratedContent) -> SeedCounts:
        counts = SeedCounts()
        problem = await self._session.scalar(
            select(Problem).where(
                Problem.source_type == "CURATED", Problem.slug == entry.problem.slug
            )
        )
        if problem is None:
            problem = Problem(source_type="CURATED", slug=entry.problem.slug, status="ACTIVE")
            self._session.add(problem)
            await self._session.flush()
            counts.problems += 1
        elif problem.status != "ACTIVE":
            raise CuratedProblemError(
                f"Problem {entry.problem.slug} conflicts with ACTIVE authored state"
            )

        problem_json = entry.problem.model_dump(mode="json")
        problem_hash = canonical_hash(problem_json)
        version = await self._session.scalar(
            select(ProblemVersion).where(
                ProblemVersion.problem_id == problem.id,
                ProblemVersion.version == entry.problem.version,
            )
        )
        if version is None:
            version = ProblemVersion(
                problem_id=problem.id,
                version=entry.problem.version,
                title=entry.problem.title,
                statement=entry.problem.statement,
                constraints_json={"items": entry.problem.constraints},
                examples_json=[item.model_dump(mode="json") for item in entry.problem.examples],
                io_schema_json={
                    "catalog_order": entry.problem.catalog_order,
                    "review_status": entry.problem.review_status,
                    "execution": entry.problem.execution.model_dump(mode="json"),
                    "languages": {
                        key: value.model_dump(mode="json")
                        for key, value in entry.problem.languages.items()
                    },
                },
                content_hash=problem_hash,
                schema_version=entry.problem.schema_version,
            )
            self._session.add(version)
            await self._session.flush()
            counts.problem_versions += 1
        elif version.content_hash != problem_hash:
            raise CuratedProblemError(
                f"{entry.problem.slug}@{entry.problem.version} changed; increment problem version"
            )

        pack_json = entry.interview_pack.model_dump(mode="json")
        pack_hash = canonical_hash(pack_json)
        pack = await self._session.scalar(
            select(InterviewPackVersion).where(
                InterviewPackVersion.problem_version_id == version.id,
                InterviewPackVersion.authored_version == entry.interview_pack.version,
            )
        )
        if pack is None:
            pack = InterviewPackVersion(
                problem_version_id=version.id,
                schema_version=entry.interview_pack.schema_version,
                authored_version=entry.interview_pack.version,
                content_hash=pack_hash,
                preparation_policy_key="curated_reviewed_v1",
                pack_json=pack_json,
                review_status=entry.interview_pack.review_status,
            )
            self._session.add(pack)
            counts.interview_pack_versions += 1
        elif pack.content_hash != pack_hash:
            raise CuratedProblemError(
                f"{entry.problem.slug} pack {entry.interview_pack.version} changed; "
                "increment pack version"
            )

        for authored in entry.problem.problem_concepts:
            concept = await self._session.scalar(
                select(Concept).where(Concept.canonical_key == authored.canonical_key)
            )
            if concept is None:
                raise CuratedProblemError(f"Unknown canonical concept {authored.canonical_key}")
            mapping = await self._session.scalar(
                select(ProblemConcept).where(
                    ProblemConcept.problem_version_id == version.id,
                    ProblemConcept.concept_id == concept.id,
                )
            )
            expected = (authored.role, authored.relevance, authored.expected_importance)
            if mapping is None:
                self._session.add(
                    ProblemConcept(
                        problem_version_id=version.id,
                        concept_id=concept.id,
                        role=authored.role,
                        relevance=authored.relevance,
                        expected_importance=authored.expected_importance,
                    )
                )
                counts.problem_concepts += 1
            elif (mapping.role, mapping.relevance, mapping.expected_importance) != expected:
                raise CuratedProblemError(
                    f"{entry.problem.slug}@{entry.problem.version} has a conflicting "
                    "immutable ProblemConcept mapping"
                )
        await self._session.flush()
        return counts

    async def list_candidate_catalog(self) -> list[ProblemVersion]:
        rows = await self._session.scalars(
            select(ProblemVersion)
            .options(joinedload(ProblemVersion.problem))
            .join(Problem)
            .where(Problem.source_type == "CURATED", Problem.status == "ACTIVE")
            .order_by(ProblemVersion.io_schema_json["catalog_order"].as_integer())
        )
        return list(rows)

    async def candidate_problem(self, problem_version_id: UUID) -> ProblemVersion:
        version = await self._session.scalar(
            select(ProblemVersion)
            .options(joinedload(ProblemVersion.problem))
            .join(Problem)
            .where(
                ProblemVersion.id == problem_version_id,
                Problem.source_type == "CURATED",
                Problem.status == "ACTIVE",
            )
        )
        if version is None:
            raise CuratedProblemError("Curated problem version is not available")
        return version

    async def reviewed_pack_for_problem(self, problem_version_id: UUID) -> InterviewPackVersion:
        pack = await self._session.scalar(
            select(InterviewPackVersion)
            .where(
                InterviewPackVersion.problem_version_id == problem_version_id,
                InterviewPackVersion.review_status == "REVIEWED",
            )
            .order_by(InterviewPackVersion.created_at.desc())
        )
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

    async def pack_subset(
        self,
        session_id: UUID,
        *,
        concept_key: str | None = None,
        approach_id: str | None = None,
        followup_id: str | None = None,
    ) -> dict[str, object]:
        pack = await self.server_pack_for_session(session_id)
        payload = pack.pack_json
        if concept_key is None and approach_id is None and followup_id is None:
            return payload
        expected_approaches = cast(list[dict[str, object]], payload.get("expected_approaches", []))
        common_followups = cast(list[dict[str, object]], payload.get("common_followups", []))
        return {
            "expected_approaches": [
                item
                for item in expected_approaches
                if (approach_id is None or item.get("approach_id") == approach_id)
                and _contains(item.get("concept_keys"), concept_key)
            ],
            "common_followups": [
                item
                for item in common_followups
                if (followup_id is None or item.get("id") == followup_id)
                and _contains(item.get("target_concepts"), concept_key)
            ],
            "invariants": payload.get("invariants", []),
        }


def _contains(values: object, target: str | None) -> bool:
    if target is None:
        return True
    return target in cast(list[str], values)

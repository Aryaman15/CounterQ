"""Immutable curated-content import and existing runtime problem queries."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from uuid import UUID

from sqlalchemy import Integer, cast, exists, func, select
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


@dataclass(frozen=True)
class ProblemConceptMapping:
    canonical_key: str
    display_name: str
    role: str
    relevance: str
    expected_importance: str | None


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
        reviewed_pack_exists = exists(
            select(InterviewPackVersion.id).where(
                InterviewPackVersion.problem_version_id == ProblemVersion.id,
                InterviewPackVersion.review_status == "REVIEWED",
            )
        )
        version_rank = (
            func.row_number()
            .over(
                partition_by=ProblemVersion.problem_id,
                order_by=cast(func.substr(ProblemVersion.version, 2), Integer).desc(),
            )
            .label("version_rank")
        )
        selectable_versions = (
            select(ProblemVersion.id.label("problem_version_id"), version_rank)
            .join(Problem)
            .where(
                Problem.source_type == "CURATED",
                Problem.status == "ACTIVE",
                ProblemVersion.io_schema_json["review_status"].as_string() == "REVIEWED",
                reviewed_pack_exists,
            )
            .subquery()
        )
        rows = await self._session.scalars(
            select(ProblemVersion)
            .options(joinedload(ProblemVersion.problem))
            .join(
                selectable_versions,
                ProblemVersion.id == selectable_versions.c.problem_version_id,
            )
            .where(selectable_versions.c.version_rank == 1)
            .order_by(
                ProblemVersion.io_schema_json["catalog_order"].as_integer(),
                ProblemVersion.problem_id,
            )
        )
        return list(rows)

    async def candidate_problem(self, problem_version_id: UUID) -> ProblemVersion:
        for version in await self.list_candidate_catalog():
            if version.id == problem_version_id:
                return version
        raise CuratedProblemError("Curated problem version is not available")

    async def reviewed_pack_for_problem(self, problem_version_id: UUID) -> InterviewPackVersion:
        packs = list(
            (
                await self._session.scalars(
                    select(InterviewPackVersion).where(
                        InterviewPackVersion.problem_version_id == problem_version_id,
                        InterviewPackVersion.review_status == "REVIEWED",
                    )
                )
            ).all()
        )
        if not packs:
            raise CuratedProblemError("No reviewed Interview Pack is available")
        return max(packs, key=lambda pack: _authored_version_key(pack.authored_version))

    async def problem_concepts_for_version(
        self,
        problem_version_id: UUID,
    ) -> list[ProblemConceptMapping]:
        rows = await self._session.execute(
            select(ProblemConcept, Concept)
            .join(Concept, ProblemConcept.concept_id == Concept.id)
            .where(ProblemConcept.problem_version_id == problem_version_id)
            .order_by(Concept.canonical_key)
        )
        return [
            ProblemConceptMapping(
                canonical_key=concept.canonical_key,
                display_name=concept.display_name,
                role=mapping.role,
                relevance=mapping.relevance,
                expected_importance=mapping.expected_importance,
            )
            for mapping, concept in rows.tuples()
        ]


def _authored_version_key(version: str) -> int:
    matched = re.fullmatch(r"v([1-9][0-9]*)", version)
    if matched is None:
        raise CuratedProblemError("Reviewed Interview Pack has an invalid authored version")
    return int(matched.group(1))

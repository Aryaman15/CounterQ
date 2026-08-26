"""Validate and transactionally import curated ontology and problem units."""

from __future__ import annotations

import asyncio

from app.db.registry import register_orm_models
from app.db.session import get_sessionmaker
from app.problems.content import validate_authored_content
from app.problems.service import CuratedProblemService, SeedCounts


async def seed_curated_content() -> SeedCounts:
    register_orm_models()
    ontology, entries = validate_authored_content()
    sessionmaker = get_sessionmaker()
    totals = SeedCounts()
    async with sessionmaker() as session:
        async with session.begin():
            totals.add(await CuratedProblemService(session).seed_ontology(ontology))
    for entry in entries:
        async with sessionmaker() as session:
            async with session.begin():
                totals.add(await CuratedProblemService(session).seed_problem(entry))
    return totals


def main() -> None:
    counts = asyncio.run(seed_curated_content())
    print(
        "Curated seed created "
        f"concepts={counts.concepts}, aliases={counts.aliases}, "
        f"relationships={counts.relationships}, problems={counts.problems}, "
        f"problem_versions={counts.problem_versions}, "
        f"interview_pack_versions={counts.interview_pack_versions}, "
        f"problem_concepts={counts.problem_concepts}."
    )


if __name__ == "__main__":
    main()

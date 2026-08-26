"""Explicit development command for importing reviewed curated content."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.db.session import get_sessionmaker
from app.problems.content import content_root, load_curated_content
from app.problems.service import CuratedProblemService


def concepts_path() -> Path:
    return content_root().parent / "concepts" / "concepts.json"


async def seed_curated_content() -> int:
    concepts = json.loads(concepts_path().read_text())
    entries = load_curated_content()
    async with get_sessionmaker()() as session:
        async with session.begin():
            service = CuratedProblemService(session)
            await service.seed_concepts(concepts)
            return await service.seed(entries)


def main() -> None:
    print(f"Seeded {asyncio.run(seed_curated_content())} curated problems.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.repository import UserRepository
from app.db.ids import uuid7
from app.db.session import get_session
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.problems.content import CuratedContent, load_curated_content, load_ontology
from app.problems.models import Problem, ProblemVersion
from app.problems.pack_service import InterviewPackService
from app.problems.repository import ProblemRepository
from app.problems.service import CuratedProblemError, CuratedProblemService

FORBIDDEN_PACK_FIELDS = {
    "expected_approaches",
    "alternative_approaches",
    "reference_solutions",
    "reference_reasoning",
    "common_followups",
    "common_misconceptions",
    "failure_modes",
    "invariants",
    "complexity_expectations",
    "counterexamples",
    "probe_opportunities",
    "constraint_mutations",
    "level_considerations",
}


async def seed_reviewed_content(db_session: AsyncSession) -> list[CuratedContent]:
    ontology = load_ontology()
    entries = load_curated_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(ontology)
    for entry in entries:
        await service.seed_problem(entry)
    return entries


async def seeded_version(
    db_session: AsyncSession,
    slug: str,
    authored_version: str | None = None,
) -> ProblemVersion:
    statement = select(ProblemVersion).join(Problem).where(Problem.slug == slug)
    if authored_version is not None:
        statement = statement.where(ProblemVersion.version == authored_version)
    versions = list((await db_session.scalars(statement)).all())
    assert versions
    if authored_version is not None:
        assert len(versions) == 1
        return versions[0]
    return max(versions, key=lambda item: int(item.version.removeprefix("v")))


def entry_by_slug(entries: list[CuratedContent], slug: str) -> CuratedContent:
    return next(entry for entry in entries if entry.problem.slug == slug)


def next_authored_version(version: str) -> str:
    return f"v{int(version.removeprefix('v')) + 1}"


async def api_client(db_session: AsyncSession) -> AsyncClient:
    app = create_app()

    async def override_session() -> AsyncSession:
        return db_session

    app.dependency_overrides[get_session] = override_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_reviewed_catalog_only_returns_current_selectable_versions(
    db_session: AsyncSession,
) -> None:
    entries = await seed_reviewed_content(db_session)
    service = CuratedProblemService(db_session)
    first = entry_by_slug(entries, "longest-substring-without-repeating-characters")

    next_entry = first.model_copy(deep=True)
    next_entry.problem.version = "v10"
    next_entry.interview_pack.version = "v10"
    next_entry.problem.statement += " Reviewed version two."
    next_entry.interview_pack.reference_reasoning += " Version two."
    await service.seed_problem(next_entry)

    inactive = "binary-search"
    inactive_version = await seeded_version(db_session, inactive)
    inactive_problem = await db_session.get(Problem, inactive_version.problem_id)
    assert inactive_problem is not None
    inactive_problem.status = "INACTIVE"

    no_pack_problem = await ProblemRepository(db_session).add_problem(
        source_type="CURATED",
        slug=f"no-pack-{uuid7()}",
        status="ACTIVE",
    )
    no_pack = await ProblemRepository(db_session).add_problem_version(
        problem=no_pack_problem,
        version="v1",
        title="No pack",
        statement="No pack",
        content_hash=f"sha256:{uuid7()}",
        schema_version="problem.v1",
    )
    no_pack.io_schema_json = {"catalog_order": 99, "review_status": "REVIEWED"}

    draft_entry = first.model_copy(deep=True)
    draft_entry.problem.slug = f"draft-{uuid7()}"
    draft_entry.problem.review_status = "DRAFT"
    draft_entry.interview_pack.review_status = "DRAFT"
    await service.seed_problem(draft_entry)
    await db_session.flush()

    catalog = await service.list_candidate_catalog()
    catalog_versions = [(item.problem.slug, item.version) for item in catalog]
    expected = [
        (entry.problem.slug, entry.problem.version)
        for entry in entries
        if entry.problem.slug not in {first.problem.slug, inactive}
    ]
    expected.append((first.problem.slug, "v10"))
    catalog_order = {entry.problem.slug: entry.problem.catalog_order for entry in entries}
    assert catalog_versions == sorted(expected, key=lambda item: catalog_order[item[0]])
    assert await service.candidate_problem(catalog[0].id) is catalog[0]
    with pytest.raises(CuratedProblemError):
        await service.candidate_problem(no_pack.id)


@pytest.mark.asyncio
async def test_reviewed_pack_resolution_uses_authored_version_not_created_timestamp(
    db_session: AsyncSession,
) -> None:
    entries = await seed_reviewed_content(db_session)
    service = CuratedProblemService(db_session)
    first = entry_by_slug(entries, "longest-substring-without-repeating-characters")
    version = await seeded_version(db_session, first.problem.slug)
    next_pack = first.model_copy(deep=True)
    next_pack.interview_pack.version = next_authored_version(first.interview_pack.version)
    next_pack.interview_pack.reference_reasoning += " A newer authored pack."
    await service.seed_problem(next_pack)

    pack = await service.reviewed_pack_for_problem(version.id)
    assert pack.authored_version == next_pack.interview_pack.version


@pytest.mark.asyncio
async def test_candidate_routes_are_database_backed_and_never_leak_pack_content(
    db_session: AsyncSession,
) -> None:
    entries = await seed_reviewed_content(db_session)
    first = entry_by_slug(entries, "longest-substring-without-repeating-characters")
    version = await seeded_version(db_session, first.problem.slug)

    client = await api_client(db_session)
    async with client:
        catalog = await client.get("/api/problems/curated")
        assert catalog.status_code == 200
        assert str(version.id) in {item["problem_version_id"] for item in catalog.json()}
        assert set(catalog.json()[0]) == {
            "problem_version_id",
            "slug",
            "title",
            "supported_languages",
            "catalog_order",
        }

        for language in ("cpp", "python", "java"):
            detail = await client.get(f"/api/problems/curated/{version.id}?language={language}")
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["selected_language"] == language
            assert payload["starter_code"]
            assert payload["return_type"] == "int"
            assert payload["comparator"] == "EXACT"
            _assert_no_pack_content(payload)

        unsupported = await client.get(f"/api/problems/curated/{version.id}?language=rust")
        assert unsupported.status_code == 422

    problem = await db_session.get(Problem, version.problem_id)
    assert problem is not None
    problem.status = "INACTIVE"
    await db_session.flush()
    client = await api_client(db_session)
    async with client:
        blocked = await client.get(f"/api/problems/curated/{version.id}?language=cpp")
    assert blocked.status_code == 404


@pytest.mark.asyncio
async def test_typed_pack_retrieval_preserves_exact_session_pack_and_supports_subsets(
    db_session: AsyncSession,
) -> None:
    entries = await seed_reviewed_content(db_session)
    curated = CuratedProblemService(db_session)
    first = entry_by_slug(entries, "longest-substring-without-repeating-characters")
    version = await seeded_version(db_session, first.problem.slug)
    original_pack = await curated.reviewed_pack_for_problem(version.id)

    newer = first.model_copy(deep=True)
    newer.interview_pack.version = next_authored_version(first.interview_pack.version)
    newer.interview_pack.reference_reasoning += " Newer pack."
    await curated.seed_problem(newer)

    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    user = await UserRepository(db_session).add(
        external_auth_provider="dev",
        external_auth_subject=f"pack-service-{uuid7()}",
    )
    interviews = InterviewRepository(db_session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1800,
        problem_source="CURATED",
    )
    interview = await interviews.add_session(
        user_id=user.id,
        configuration_id=configuration.id,
        problem_version_id=version.id,
        interview_pack_version_id=original_pack.id,
        current_stage="IMPLEMENTATION",
        state_version=0,
        status="ACTIVE",
        started_at=now,
        deadline_at=now + timedelta(minutes=30),
    )
    await interviews.add_budget(
        session_id=interview.id,
        max_duration_seconds=1800,
        max_probes=5,
        max_deep_reasoning_calls=8,
        max_strong_reasoning_calls=1,
        max_vision_calls=0,
        soft_monetary_budget=Decimal("2.5"),
        hard_monetary_budget=Decimal("5"),
        realtime_reserved_budget=Decimal("1.25"),
    )

    packs = InterviewPackService(db_session)
    current = await packs.for_problem_version(version.id)
    historical = await packs.for_session(interview.id)
    assert current.version == newer.interview_pack.version
    assert historical.version == first.interview_pack.version

    invariant_concept = "sliding_window_invariant"
    complexity_concept = "hash_table_lookup"
    assert packs.approaches_by_concept(historical, invariant_concept)
    approach = packs.approach_by_id(historical, historical.expected_approaches[0].approach_id)
    assert approach is not None
    assert packs.invariants_by_concept(historical, invariant_concept)
    assert packs.misconceptions_by_concept(historical, complexity_concept)
    assert packs.failure_modes_by_concept(historical, invariant_concept)
    assert packs.probe_opportunities_by_concept(historical, invariant_concept)
    followups = packs.common_followups_by_concept(historical, invariant_concept)
    assert followups
    assert packs.common_followup_by_id(historical, followups[0].id) == followups[0]

    mappings = await curated.problem_concepts_for_version(version.id)
    assert {(item.canonical_key, item.role) for item in mappings} == {
        (item.canonical_key, item.role) for item in first.problem.problem_concepts
    }


@pytest.mark.asyncio
async def test_malformed_persisted_pack_fails_explicitly(db_session: AsyncSession) -> None:
    entries = await seed_reviewed_content(db_session)
    first = entry_by_slug(entries, "longest-substring-without-repeating-characters")
    version = await seeded_version(db_session, first.problem.slug)
    pack = await CuratedProblemService(db_session).reviewed_pack_for_problem(version.id)
    pack.pack_json = deepcopy(pack.pack_json)
    pack.pack_json.pop("reference_solutions")
    await db_session.flush()

    with pytest.raises(CuratedProblemError, match="Persisted Interview Pack is invalid"):
        await InterviewPackService(db_session).for_problem_version(version.id)


def _assert_no_pack_content(payload: object) -> None:
    serialized = json.dumps(payload)
    for field in FORBIDDEN_PACK_FIELDS:
        assert f'"{field}"' not in serialized

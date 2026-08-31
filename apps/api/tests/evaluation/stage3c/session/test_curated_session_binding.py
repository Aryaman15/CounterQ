from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.repository import UserRepository
from app.config.settings import create_settings, get_settings
from app.db.session import get_session
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.repository import InterviewRepository
from app.main import create_app
from app.problems.content import CuratedContent, load_curated_content, load_ontology
from app.problems.models import Problem, ProblemVersion
from app.problems.service import CuratedProblemService

FORBIDDEN_PACK_FIELDS = {
    "expected_approaches",
    "alternative_approaches",
    "reference_solutions",
    "reference_reasoning",
    "common_followups",
    "common_misconceptions",
    "failure_modes",
    "invariants",
    "counterexamples",
    "probe_opportunities",
    "constraint_mutations",
    "level_considerations",
}


async def _seed_entry(
    db_session: AsyncSession, index: int = 0
) -> tuple[CuratedContent, ProblemVersion]:
    entry = load_curated_content()[index].model_copy(deep=True)
    service = CuratedProblemService(db_session)
    await service.seed_ontology(load_ontology())
    await service.seed_problem(entry)
    version = await db_session.scalar(
        select(ProblemVersion)
        .join(Problem)
        .where(Problem.slug == entry.problem.slug)
        .where(ProblemVersion.version == entry.problem.version)
    )
    assert version is not None
    return entry, version


async def _client(db_session: AsyncSession, tmp_path: Path) -> AsyncClient:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_curated_creation_binds_exact_versions_and_historical_restore_never_drifts(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    entry, original_version = await _seed_entry(db_session)
    original_pack = await CuratedProblemService(db_session).reviewed_pack_for_problem(
        original_version.id
    )
    old_statement = original_version.statement
    old_examples = original_version.examples_json
    old_starter = entry.problem.languages["python"].starter_code
    old_signature = entry.problem.languages["python"].display_signature
    await db_session.commit()

    client = await _client(db_session, tmp_path)
    async with client:
        created = await client.post(
            "/api/realtime/development-interview",
            json={
                "purpose": "interview_demo",
                "problem_version_id": str(original_version.id),
                "language": "python",
                "client_instance_id": "stage3c-session-gate",
            },
        )
        assert created.status_code == 200, created.text
        created_payload = created.json()
        session_id = created_payload["interview_session_id"]
        assert created_payload["restoration"] == "CREATED"
        assert created_payload["language"] == "python"
        assert created_payload["problem"]["problem_version_id"] == str(original_version.id)
        assert created_payload["problem"]["statement"] == old_statement
        assert created_payload["problem"]["examples"] == old_examples
        assert created_payload["problem"]["starter_code"] == old_starter
        assert created_payload["problem"]["display_signature"] == old_signature
        _assert_candidate_safe(created_payload["problem"])

        bound = await db_session.get(InterviewSession, session_id)
        assert bound is not None
        assert bound.problem_version_id == original_version.id
        assert bound.interview_pack_version_id == original_pack.id
        configuration = await db_session.get(
            InterviewConfiguration, bound.interview_configuration_id
        )
        assert configuration is not None
        assert configuration.language == "python"

        newer = entry.model_copy(deep=True)
        newer.problem.version = "v2"
        newer.problem.statement += " New reviewed wording."
        newer.problem.examples[0].explanation += " Version two."
        newer.problem.languages["python"].starter_code += "\n# version two"
        newer.problem.languages["python"].display_signature += "  # v2"
        newer.interview_pack.version = "v2"
        newer.interview_pack.reference_reasoning += " Version two."
        await CuratedProblemService(db_session).seed_problem(newer)
        await db_session.commit()

        restored = await client.post(
            "/api/realtime/development-interview",
            json={
                "purpose": "interview_demo",
                "interview_session_id": session_id,
                "client_instance_id": "stage3c-session-gate",
            },
        )
        assert restored.status_code == 200, restored.text
        restored_payload = restored.json()
        assert restored_payload["restoration"] == "RESTORED"
        assert restored_payload["problem"]["problem_version_id"] == str(original_version.id)
        assert restored_payload["problem"]["statement"] == old_statement
        assert restored_payload["problem"]["examples"] == old_examples
        assert restored_payload["problem"]["starter_code"] == old_starter
        assert restored_payload["problem"]["display_signature"] == old_signature
        _assert_candidate_safe(restored_payload["problem"])

        rebound = await db_session.get(InterviewSession, session_id)
        assert rebound is not None
        assert rebound.problem_version_id == original_version.id
        assert rebound.interview_pack_version_id == original_pack.id
        await db_session.commit()

        stale_selection = await client.post(
            "/api/realtime/development-interview",
            json={
                "purpose": "interview_demo",
                "problem_version_id": str(original_version.id),
                "language": "python",
            },
        )
        assert stale_selection.status_code == 422


@pytest.mark.asyncio
async def test_wrong_problem_pack_cannot_bind_through_session_repository(
    db_session: AsyncSession,
) -> None:
    _, first_version = await _seed_entry(db_session, 0)
    _, second_version = await _seed_entry(db_session, 1)
    wrong_pack = await CuratedProblemService(db_session).reviewed_pack_for_problem(
        second_version.id
    )
    user = await UserRepository(db_session).add(
        external_auth_provider="dev",
        external_auth_subject="stage3c-wrong-pack-gate",
    )
    interviews = InterviewRepository(db_session)
    configuration = await interviews.add_configuration(
        mode="SIMULATION",
        level="NEW_GRAD",
        language="cpp",
        configured_duration_seconds=1800,
        problem_source="CURATED",
    )

    with pytest.raises(ValueError, match="must belong"):
        await interviews.add_session(
            user_id=user.id,
            configuration_id=configuration.id,
            problem_version_id=first_version.id,
            interview_pack_version_id=wrong_pack.id,
            current_stage="IMPLEMENTATION",
            state_version=0,
            status="ACTIVE",
            started_at=first_version.created_at,
            deadline_at=first_version.created_at.replace(year=first_version.created_at.year + 1),
        )


def _assert_candidate_safe(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in FORBIDDEN_PACK_FIELDS:
        assert f'"{forbidden}"' not in serialized

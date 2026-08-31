from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import create_settings, get_settings
from app.db.session import get_session
from app.execution.routes import DevelopmentRunResponse
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.main import create_app
from app.problems.content import (
    SUPPORTED_LANGUAGES,
    CuratedContent,
    load_curated_content,
    load_ontology,
)
from app.problems.models import InterviewPackVersion, Problem, ProblemVersion
from app.problems.service import CuratedProblemService

FORBIDDEN_CANDIDATE_KEYS = {
    "expected_approaches",
    "alternative_approaches",
    "reference_solutions",
    "reference_reasoning",
    "common_followups",
    "common_misconceptions",
    "complexity_expectations",
    "edge_cases",
    "failure_modes",
    "invariants",
    "concepts",
    "counterexamples",
    "probe_opportunities",
    "constraint_mutations",
    "level_considerations",
    "pack_json",
    "interview_pack",
    "interview_pack_version",
    "interview_pack_version_id",
}


async def _seed_current_catalog(
    db_session: AsyncSession,
) -> tuple[list[CuratedContent], dict[str, ProblemVersion]]:
    entries = load_curated_content()
    service = CuratedProblemService(db_session)
    await service.seed_ontology(load_ontology())
    for entry in entries:
        await service.seed_problem(entry)
    by_identity: dict[str, ProblemVersion] = {}
    for entry in entries:
        version = await db_session.scalar(
            select(ProblemVersion)
            .join(Problem)
            .where(
                Problem.source_type == "CURATED",
                Problem.slug == entry.problem.slug,
                ProblemVersion.version == entry.problem.version,
            )
        )
        assert version is not None
        by_identity[f"{entry.problem.slug}@{entry.problem.version}"] = version
    await db_session.commit()
    return entries, by_identity


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
async def test_all_current_problem_language_sessions_bind_and_restore_exact_candidate_safe_truth(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    entries, versions = await _seed_current_catalog(db_session)
    assert len(entries) == 20
    assert len(versions) == 20
    assert SUPPORTED_LANGUAGES == {"cpp", "python", "java"}

    client = await _client(db_session, tmp_path)
    async with client:
        catalog_response = await client.get("/api/problems/curated")
        assert catalog_response.status_code == 200, catalog_response.text
        catalog = catalog_response.json()
        assert len(catalog) == 20
        _assert_candidate_safe(catalog)
        selectable_ids = {item["problem_version_id"] for item in catalog}
        await db_session.commit()

        combinations = 0
        for entry in entries:
            identity = f"{entry.problem.slug}@{entry.problem.version}"
            version = versions[identity]
            assert str(version.id) in selectable_ids
            pack = await CuratedProblemService(db_session).reviewed_pack_for_problem(
                version.id
            )
            assert pack.problem_version_id == version.id
            assert pack.review_status == "REVIEWED"
            await db_session.commit()

            for language in ("cpp", "python", "java"):
                combinations += 1
                authored_language = entry.problem.languages[language]
                method_name = entry.problem.execution.method_name
                assert authored_language.starter_code.strip()
                assert authored_language.display_signature.strip()
                assert method_name in authored_language.starter_code
                assert method_name in authored_language.display_signature

                detail_response = await client.get(
                    f"/api/problems/curated/{version.id}?language={language}"
                )
                assert detail_response.status_code == 200, detail_response.text
                detail = detail_response.json()
                _assert_exact_candidate_problem(detail, entry, version, language)
                _assert_candidate_safe(detail)
                await db_session.commit()

                rejected_pack_selection = await client.post(
                    "/api/realtime/development-interview",
                    json={
                        "purpose": "interview_demo",
                        "problem_version_id": str(version.id),
                        "language": language,
                        "client_instance_id": f"acceptance-{combinations}",
                        "interview_pack_version_id": str(uuid4()),
                    },
                )
                assert rejected_pack_selection.status_code == 422
                assert any(
                    error["loc"][-1] == "interview_pack_version_id"
                    for error in rejected_pack_selection.json()["detail"]
                )

                created_response = await client.post(
                    "/api/realtime/development-interview",
                    json={
                        "purpose": "interview_demo",
                        "problem_version_id": str(version.id),
                        "language": language,
                        "client_instance_id": f"acceptance-{combinations}",
                    },
                )
                assert created_response.status_code == 200, created_response.text
                created = created_response.json()
                assert created["restoration"] == "CREATED"
                assert created["language"] == language
                _assert_exact_candidate_problem(created["problem"], entry, version, language)
                _assert_candidate_safe(created)

                session = await db_session.get(
                    InterviewSession, created["interview_session_id"]
                )
                assert session is not None
                assert session.problem_version_id == version.id
                assert session.interview_pack_version_id == pack.id
                bound_pack = await db_session.get(
                    InterviewPackVersion, session.interview_pack_version_id
                )
                assert bound_pack is not None
                assert bound_pack.problem_version_id == version.id
                configuration = await db_session.get(
                    InterviewConfiguration, session.interview_configuration_id
                )
                assert configuration is not None
                assert configuration.language == language
                await db_session.commit()

                restored_response = await client.post(
                    "/api/realtime/development-interview",
                    json={
                        "purpose": "interview_demo",
                        "interview_session_id": created["interview_session_id"],
                        "client_instance_id": f"acceptance-{combinations}",
                    },
                )
                assert restored_response.status_code == 200, restored_response.text
                restored = restored_response.json()
                assert restored["restoration"] == "RESTORED"
                assert restored["interview_session_id"] == created["interview_session_id"]
                assert restored["language"] == language
                _assert_exact_candidate_problem(
                    restored["problem"], entry, version, language
                )
                _assert_candidate_safe(restored)

        assert combinations == 60


def test_visible_and_custom_execution_contracts_preserve_candidate_safe_semantics() -> None:
    visible = DevelopmentRunResponse.model_validate(
        _execution_payload(
            run_kind="VISIBLE",
            expected_output="[0,1]",
            expected_output_value=[0, 1],
            actual_output="[0,1]",
            actual_output_value=[0, 1],
            comparison_kind="EXPECTED",
            case_status="PASSED",
        )
    ).model_dump(mode="json")
    custom = DevelopmentRunResponse.model_validate(
        _execution_payload(
            run_kind="CUSTOM",
            expected_output=None,
            expected_output_value=None,
            actual_output="[0,1]",
            actual_output_value=[0, 1],
            comparison_kind="NONE",
            case_status="EXECUTED",
        )
    ).model_dump(mode="json")

    assert visible["cases"][0]["comparison_kind"] == "EXPECTED"
    assert visible["cases"][0]["status"] == "PASSED"
    assert custom["cases"][0]["expected_output"] is None
    assert custom["cases"][0]["expected_output_value"] is None
    assert custom["cases"][0]["comparison_kind"] == "NONE"
    assert custom["cases"][0]["status"] == "EXECUTED"
    _assert_candidate_safe(visible)
    _assert_candidate_safe(custom)


def _assert_exact_candidate_problem(
    payload: dict[str, object],
    entry: CuratedContent,
    version: ProblemVersion,
    language: str,
) -> None:
    authored_language = entry.problem.languages[language]  # type: ignore[index]
    assert payload["problem_version_id"] == str(version.id)
    assert payload["slug"] == entry.problem.slug
    assert payload["title"] == entry.problem.title
    assert payload["statement"] == entry.problem.statement
    assert payload["constraints"] == entry.problem.constraints
    assert payload["examples"] == [
        example.model_dump(mode="json") for example in entry.problem.examples
    ]
    assert payload["supported_languages"] == ["cpp", "python", "java"]
    assert payload["selected_language"] == language
    assert payload["display_signature"] == authored_language.display_signature
    assert payload["starter_code"] == authored_language.starter_code
    assert payload["argument_schema"] == [
        argument.model_dump(mode="json") for argument in entry.problem.execution.arguments
    ]
    assert payload["return_type"] == entry.problem.execution.return_type
    assert payload["comparator"] == entry.problem.execution.comparator
    assert payload["custom_test_supported"] == entry.problem.execution.custom_test_supported


def _assert_candidate_safe(payload: object) -> None:
    keys = _recursive_keys(payload)
    assert keys.isdisjoint(FORBIDDEN_CANDIDATE_KEYS)
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in FORBIDDEN_CANDIDATE_KEYS:
        assert f'"{forbidden}"' not in serialized


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested_key for nested in value.values() for nested_key in _recursive_keys(nested)
        }
    if isinstance(value, list):
        return {nested_key for nested in value for nested_key in _recursive_keys(nested)}
    return set()


def _execution_payload(
    *,
    run_kind: str,
    expected_output: str | None,
    expected_output_value: object,
    actual_output: str,
    actual_output_value: object,
    comparison_kind: str,
    case_status: str,
) -> dict[str, object]:
    return {
        "execution_run_id": str(uuid4()),
        "code_snapshot_id": str(uuid4()),
        "code_snapshot_version": 1,
        "run_kind": run_kind,
        "status": "SUCCEEDED",
        "stdout": "",
        "stderr": "",
        "compiler_output": "",
        "exit_code": 0,
        "timed_out": False,
        "output_truncated": False,
        "duration_ms": 2,
        "cases": [
            {
                "identifier": "visible-1" if run_kind == "VISIBLE" else "custom-1",
                "input_json": {"nums": [2, 7], "target": 9},
                "expected_output": expected_output,
                "actual_output": actual_output,
                "expected_output_value": expected_output_value,
                "actual_output_value": actual_output_value,
                "comparison_kind": comparison_kind,
                "status": case_status,
                "duration_ms": 1,
                "failure_classification": None,
            }
        ],
    }

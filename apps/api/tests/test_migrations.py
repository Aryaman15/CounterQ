import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.config.settings import get_settings


def test_alembic_configuration_has_stage9d_interview_deletion_head() -> None:
    config = Config(str(Path("alembic.ini")))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "202609110122"


def test_full_migration_chain_downgrades_and_upgrades_cleanly() -> None:
    database_name = f"counterq_migration_test_{uuid4().hex}"
    database_url = _database_url(database_name)
    original_database_url = os.environ.get("DATABASE_URL")
    config = Config(str(Path("alembic.ini")))

    asyncio.run(create_database(database_name))
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "202608230001")
        assert asyncio.run(table_exists("users", database_url=database_url)) is False

        command.upgrade(config, "head")
        assert asyncio.run(
            table_exists("interview_sessions", database_url=database_url)
        ) is True
    finally:
        asyncio.run(drop_database(database_name))
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def test_stage9a_table_boundary_is_explicit() -> None:
    table_names = asyncio.run(public_table_names())

    assert {
        "ai_policy_versions",
        "ai_invocations",
        "candidate_claims",
        "candidate_profiles",
        "candidate_responses",
        "candidate_response_sources",
        "assessment_sources",
        "assessment_unit_evaluations",
        "assessments",
        "breakpoint_evidence",
        "breakpoints",
        "code_diffs",
        "code_snapshots",
        "concept_aliases",
        "concept_relationships",
        "concepts",
        "countermap_projections",
        "concept_mastery",
        "concept_mastery_evidence",
        "examiner_decisions",
        "evidence",
        "evidence_concepts",
        "evidence_skills",
        "evidence_sources",
        "execution_runs",
        "interview_configurations",
        "interview_events",
        "interview_pack_versions",
        "interview_stage_transitions",
        "interviewer_prompt_deliveries",
        "interviewer_prompts",
        "interview_sessions",
        "mastery_transition_evidence",
        "mastery_transitions",
        "outbox_events",
        "problem_versions",
        "retest_attempt_evidence",
        "retest_attempts",
        "retest_recommendations",
        "problem_concepts",
        "problems",
        "session_budgets",
        "session_reports",
        "skill_dimensions",
        "skill_mastery",
        "skill_mastery_evidence",
        "transcript_segments",
        "test_results",
        "users",
    }.issubset(table_names)
    assert {
        "countermap_edges",
        "countermap_nodes",
        "mastery_snapshots",
    }.isdisjoint(table_names)


def test_stage9a_candidate_profile_columns_are_explicit() -> None:
    columns = asyncio.run(table_columns("candidate_profiles"))

    assert columns == {
        "created_at",
        "default_interview_mode",
        "display_name",
        "interview_level",
        "preferred_language",
        "profile_version",
        "target_role",
        "timezone",
        "updated_at",
        "user_id",
    }


def test_stage9a_candidate_profile_constraints_preserve_one_to_one_domain() -> None:
    constraints = asyncio.run(table_constraints("candidate_profiles"))

    assert constraints == {
        "pk_candidate_profiles": ("PRIMARY KEY", "PRIMARY KEY (user_id)"),
        "fk_candidate_profiles_user_id_users": (
            "FOREIGN KEY",
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        ),
        "ck_candidate_profiles_default_interview_mode": (
            "CHECK",
            "CHECK (((default_interview_mode)::text = ANY "
            "((ARRAY['COACH'::character varying, 'SIMULATION'::character varying])::text[])))",
        ),
        "ck_candidate_profiles_interview_level": (
            "CHECK",
            "CHECK (((interview_level)::text = ANY ((ARRAY['INTERN'::character varying, "
            "'NEW_GRAD'::character varying, 'EARLY_CAREER'::character varying])::text[])))",
        ),
        "ck_candidate_profiles_preferred_language": (
            "CHECK",
            "CHECK (((preferred_language)::text = ANY ((ARRAY['cpp'::character varying, "
            "'java'::character varying, 'python'::character varying])::text[])))",
        ),
        "ck_candidate_profiles_profile_version_positive": (
            "CHECK",
            "CHECK ((profile_version > 0))",
        ),
    }


def test_stage6b_report_budget_columns_are_safe_for_existing_sessions() -> None:
    columns = asyncio.run(session_budget_report_columns())

    assert columns == {
        "max_report_reasoning_calls": ("NO", "4"),
        "report_reasoning_used": ("NO", "0"),
    }


def test_stage6b_unit_evaluation_ledger_is_content_free() -> None:
    columns = asyncio.run(table_columns("assessment_unit_evaluations"))

    assert columns == {
        "completed_at",
        "created_at",
        "evaluator_policy_version_id",
        "finding_count",
        "id",
        "interview_session_id",
        "successful_ai_invocation_id",
        "unit_key",
        "unit_kind",
    }


def test_stage7a_countermap_projection_columns_are_explicit() -> None:
    columns = asyncio.run(table_columns("countermap_projections"))

    assert columns == {
        "created_at",
        "generated_at",
        "generation_policy_version",
        "generation_request_key",
        "graph_json",
        "id",
        "interview_session_id",
        "is_current",
        "last_failure_category",
        "projection_version",
        "schema_version",
        "source_identity",
        "source_watermark",
        "status",
        "updated_at",
    }


async def table_exists(table_name: str, *, database_url: str | None = None) -> bool:
    return table_name in await public_table_names(database_url=database_url)


async def public_table_names(*, database_url: str | None = None) -> set[str]:
    connection = await asyncpg.connect(_asyncpg_url(database_url or _database_url()))
    try:
        rows = await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            """,
        )
        return {str(row["table_name"]) for row in rows}
    finally:
        await connection.close()


async def create_database(database_name: str) -> None:
    connection = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def drop_database(database_name: str) -> None:
    connection = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
    finally:
        await connection.close()


def _database_url(database_name: str | None = None) -> str:
    url = make_url(get_settings().database_url)
    if database_name is not None:
        url = url.set(database=database_name)
    return url.render_as_string(hide_password=False)


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def table_columns(table_name: str) -> set[str]:
    database_url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = $1
            """,
            table_name,
        )
        return {str(row["column_name"]) for row in rows}
    finally:
        await connection.close()


async def session_budget_report_columns() -> dict[str, tuple[str, str | None]]:
    database_url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'session_budgets'
            AND column_name IN ('max_report_reasoning_calls', 'report_reasoning_used')
            ORDER BY column_name
            """,
        )
        return {
            str(row["column_name"]): (
                str(row["is_nullable"]),
                str(row["column_default"]) if row["column_default"] is not None else None,
            )
            for row in rows
        }
    finally:
        await connection.close()


async def table_constraints(table_name: str) -> dict[str, tuple[str, str]]:
    database_url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT
                constraint_name,
                constraint_type,
                pg_get_constraintdef(pg_constraint.oid) AS definition
            FROM information_schema.table_constraints
            JOIN pg_constraint
              ON pg_constraint.conname = constraint_name
            WHERE table_schema = 'public'
              AND table_name = $1
            ORDER BY constraint_name
            """,
            table_name,
        )
        return {
            str(row["constraint_name"]): (
                str(row["constraint_type"]),
                str(row["definition"]),
            )
            for row in rows
        }
    finally:
        await connection.close()

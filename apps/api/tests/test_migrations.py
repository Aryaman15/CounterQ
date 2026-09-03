import asyncio
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config.settings import get_settings


def test_alembic_configuration_has_stage6b_report_budget_head() -> None:
    config = Config(str(Path("alembic.ini")))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "202609040117"


def test_full_migration_chain_downgrades_and_upgrades_cleanly() -> None:
    config = Config(str(Path("alembic.ini")))

    try:
        command.downgrade(config, "202608230001")
        assert asyncio.run(table_exists("users")) is False

        command.upgrade(config, "head")
        assert asyncio.run(table_exists("interview_sessions")) is True
    finally:
        command.upgrade(config, "head")


def test_stage6b_table_boundary_is_explicit() -> None:
    table_names = asyncio.run(public_table_names())

    assert {
        "ai_policy_versions",
        "ai_invocations",
        "candidate_claims",
        "candidate_responses",
        "candidate_response_sources",
        "assessment_sources",
        "assessments",
        "breakpoint_evidence",
        "breakpoints",
        "code_diffs",
        "code_snapshots",
        "concept_aliases",
        "concept_relationships",
        "concepts",
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
        "outbox_events",
        "problem_versions",
        "problem_concepts",
        "problems",
        "session_budgets",
        "session_reports",
        "skill_dimensions",
        "transcript_segments",
        "test_results",
        "users",
    }.issubset(table_names)
    assert {
        "candidate_profiles",
        "concept_mastery",
        "countermap_edges",
        "countermap_nodes",
        "countermap_projections",
        "retest_recommendations",
    }.isdisjoint(table_names)


def test_stage6b_report_budget_columns_are_safe_for_existing_sessions() -> None:
    columns = asyncio.run(session_budget_report_columns())

    assert columns == {
        "max_report_reasoning_calls": ("NO", "4"),
        "report_reasoning_used": ("NO", "0"),
    }


async def table_exists(table_name: str) -> bool:
    return table_name in await public_table_names()


async def public_table_names() -> set[str]:
    database_url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
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

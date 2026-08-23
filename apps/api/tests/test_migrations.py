import asyncio
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config.settings import get_settings


def test_alembic_configuration_has_stage1_2_head() -> None:
    config = Config(str(Path("alembic.ini")))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "202608230106"


def test_stage1_2_migration_downgrades_and_upgrades_cleanly() -> None:
    config = Config(str(Path("alembic.ini")))

    try:
        command.downgrade(config, "202608230001")
        assert asyncio.run(table_exists("users")) is False

        command.upgrade(config, "head")
        assert asyncio.run(table_exists("interview_sessions")) is True
    finally:
        command.upgrade(config, "head")


def test_stage1_2_table_boundary_is_explicit() -> None:
    table_names = asyncio.run(public_table_names())

    assert {
        "ai_policy_versions",
        "ai_invocations",
        "candidate_claims",
        "candidate_responses",
        "candidate_response_sources",
        "code_diffs",
        "code_snapshots",
        "examiner_decisions",
        "interview_configurations",
        "interview_events",
        "interview_pack_versions",
        "interview_stage_transitions",
        "interviewer_prompt_deliveries",
        "interviewer_prompts",
        "interview_sessions",
        "problem_versions",
        "problems",
        "session_budgets",
        "transcript_segments",
        "users",
    }.issubset(table_names)
    assert {
        "assessments",
        "breakpoints",
        "candidate_profiles",
        "evidence",
        "execution_runs",
    }.isdisjoint(table_names)


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

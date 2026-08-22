import asyncio
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config.settings import get_settings


def test_alembic_configuration_has_stage1_1a_head() -> None:
    config = Config(str(Path("alembic.ini")))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == "202608230102"


def test_stage1_1a_migration_downgrades_and_upgrades_cleanly() -> None:
    config = Config(str(Path("alembic.ini")))

    try:
        command.downgrade(config, "202608230001")
        assert asyncio.run(table_exists("users")) is False

        command.upgrade(config, "head")
        assert asyncio.run(table_exists("interview_sessions")) is True
    finally:
        command.upgrade(config, "head")


def test_stage1_1a_table_boundary_is_explicit() -> None:
    table_names = asyncio.run(public_table_names())

    assert {
        "ai_policy_versions",
        "code_snapshots",
        "interview_configurations",
        "interview_events",
        "interview_pack_versions",
        "interview_sessions",
        "problem_versions",
        "problems",
        "session_budgets",
        "transcript_segments",
        "users",
    }.issubset(table_names)
    assert {
        "ai_invocations",
        "assessments",
        "breakpoints",
        "candidate_claims",
        "candidate_profiles",
        "candidate_responses",
        "evidence",
        "examiner_decisions",
        "interviewer_prompt_deliveries",
        "interviewer_prompts",
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

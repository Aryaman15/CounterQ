from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.db.session import build_engine


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    command.upgrade(Config("alembic.ini"), "head")
    yield


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = build_engine()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()

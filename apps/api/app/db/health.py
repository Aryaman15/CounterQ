from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import get_engine


async def check_postgres(engine: AsyncEngine | None = None) -> bool:
    active_engine = engine or get_engine()
    async with active_engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        value = result.scalar_one()
        return bool(value == 1)

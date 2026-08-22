from app.db.health import check_postgres
from app.db.session import build_engine
from app.redis.client import check_redis


async def test_postgres_connectivity() -> None:
    engine = build_engine()
    try:
        assert await check_postgres(engine)
    finally:
        await engine.dispose()


async def test_redis_connectivity() -> None:
    assert await check_redis()

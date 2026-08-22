from app.db.health import check_postgres
from app.redis.client import check_redis


async def test_postgres_connectivity() -> None:
    assert await check_postgres()


async def test_redis_connectivity() -> None:
    assert await check_redis()


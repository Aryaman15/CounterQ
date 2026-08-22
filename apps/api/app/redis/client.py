from redis.asyncio import Redis

from app.config.settings import Settings, get_settings


def build_redis(settings: Settings | None = None) -> Redis:
    active_settings = settings or get_settings()
    return Redis.from_url(active_settings.redis_url, encoding="utf-8", decode_responses=True)


async def check_redis(client: Redis | None = None) -> bool:
    active_client = client or build_redis()
    try:
        return bool(await active_client.ping())
    finally:
        if client is None:
            await active_client.aclose()


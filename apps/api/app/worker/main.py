import argparse
import asyncio
import signal

import structlog

from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.redis.client import check_redis

logger = structlog.get_logger(__name__)


async def run(check_once: bool = False) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("worker_starting", app_env=settings.app_env)

    if check_once:
        redis_ok = await check_redis()
        logger.info("worker_startup_check_complete", redis_ok=redis_ok)
        return 0 if redis_ok else 1

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    logger.info("worker_ready")
    await stop_event.wait()
    logger.info("worker_stopping")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CounterQ background worker process.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run startup dependency checks once and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(check_once=args.check))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

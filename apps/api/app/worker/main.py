import argparse
import asyncio
import threading

import structlog
from redis import Redis
from rq import SimpleWorker
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.db.registry import register_orm_models
from app.db.session import build_engine
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.publisher import RQJobPublisher
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

    logger.info("worker_ready")
    return 0


async def _run_dispatcher(stop_event: threading.Event) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    dispatcher = OutboxDispatcher(
        sessionmaker=sessionmaker,
        publisher=RQJobPublisher(settings),
        max_attempts=settings.outbox_max_attempts,
        claim_lease_seconds=settings.outbox_claim_lease_seconds,
    )
    try:
        while not stop_event.is_set():
            await dispatcher.dispatch_once(limit=settings.outbox_batch_size)
            await asyncio.to_thread(stop_event.wait, settings.outbox_poll_interval_seconds)
    finally:
        await engine.dispose()


def _run_worker_process() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_orm_models()
    stop_event = threading.Event()
    dispatcher_thread = threading.Thread(
        target=lambda: asyncio.run(_run_dispatcher(stop_event)),
        name="counterq-outbox-dispatcher",
        daemon=True,
    )
    dispatcher_thread.start()
    connection = Redis.from_url(settings.redis_url)
    worker = SimpleWorker([settings.background_queue_name], connection=connection)
    logger.info(
        "worker_ready",
        queue=settings.background_queue_name,
        outbox_dispatcher=True,
    )
    try:
        worker.work(with_scheduler=False)
    finally:
        stop_event.set()
        dispatcher_thread.join(timeout=5)
        connection.close()
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
        if args.check:
            return asyncio.run(run(check_once=True))
        return _run_worker_process()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

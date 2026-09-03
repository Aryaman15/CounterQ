"""Replaceable Redis-backed background-job publication boundary."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from redis import Redis
from rq import Queue

from app.config.settings import Settings, get_settings


class BackgroundJobPublisher(Protocol):
    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        """Publish one idempotent outbox-consumer job."""


class RQJobPublisher:
    def __init__(self, settings: Settings | None = None) -> None:
        active = settings or get_settings()
        connection = Redis.from_url(active.redis_url)
        self._queue = Queue(active.background_queue_name, connection=connection)

    async def publish(self, *, outbox_event_id: UUID, attempt: int) -> None:
        await asyncio.to_thread(
            self._queue.enqueue,
            "app.worker.jobs.consume_outbox_event",
            str(outbox_event_id),
            job_id=f"outbox-{outbox_event_id}-attempt-{attempt}",
            result_ttl=3600,
            failure_ttl=86400,
        )

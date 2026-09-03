"""PostgreSQL outbox claiming and at-least-once Redis publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.outbox.models import OutboxEvent
from app.outbox.publisher import BackgroundJobPublisher

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    published: int
    retryable: int
    failed: int


class OutboxDispatcher:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        publisher: BackgroundJobPublisher,
        max_attempts: int = 5,
        claim_lease_seconds: int = 120,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._publisher = publisher
        self._max_attempts = max_attempts
        self._claim_lease_seconds = claim_lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_once(self, *, limit: int = 20) -> DispatchResult:
        now = self._clock()
        published = retryable = failed = 0
        async with self._sessionmaker() as session:
            async with session.begin():
                events = list(
                    await session.scalars(
                        select(OutboxEvent)
                        .where(_dispatchable_at(now))
                        .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                for event in events:
                    event.status = "PROCESSING"
                    event.attempt_count += 1
                    event.last_attempt_at = now
                    event.next_retry_at = now + timedelta(seconds=self._claim_lease_seconds)
                    try:
                        await self._publisher.publish(
                            outbox_event_id=event.id,
                            attempt=event.attempt_count,
                        )
                    except Exception as exc:
                        event.last_error = type(exc).__name__[:256]
                        if event.attempt_count >= self._max_attempts:
                            event.status = "FAILED"
                            event.next_retry_at = None
                            failed += 1
                        else:
                            event.status = "RETRY"
                            event.next_retry_at = now + _retry_delay(event.attempt_count)
                            retryable += 1
                        logger.warning(
                            "outbox_publish_failed",
                            outbox_event_id=str(event.id),
                            event_type=event.event_type,
                            attempt=event.attempt_count,
                            status=event.status,
                            error_class=type(exc).__name__,
                        )
                    else:
                        event.status = "PUBLISHED"
                        event.published_at = now
                        event.last_error = None
                        published += 1
        return DispatchResult(len(events), published, retryable, failed)


async def run_dispatcher_loop(
    dispatcher: OutboxDispatcher,
    *,
    poll_interval_seconds: float,
    batch_size: int,
    stop_event: asyncio.Event | None = None,
) -> None:
    stopping = stop_event or asyncio.Event()
    while not stopping.is_set():
        result = await dispatcher.dispatch_once(limit=batch_size)
        if result.claimed:
            logger.info(
                "outbox_dispatch_batch",
                claimed=result.claimed,
                published=result.published,
                retryable=result.retryable,
                failed=result.failed,
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            pass


def _dispatchable_at(now: datetime):  # type: ignore[no-untyped-def]
    initial = and_(
        OutboxEvent.status.in_(("PENDING", "RETRY")),
        OutboxEvent.available_at <= now,
        or_(OutboxEvent.next_retry_at.is_(None), OutboxEvent.next_retry_at <= now),
    )
    abandoned = and_(
        OutboxEvent.status.in_(("PUBLISHED", "PROCESSING")),
        OutboxEvent.next_retry_at.is_not(None),
        OutboxEvent.next_retry_at <= now,
    )
    return or_(initial, abandoned)


def _retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** min(attempt, 8)))

"""Small application boundary for durable eventual-work intentions."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.outbox.models import OutboxEvent


class OutboxIdempotencyConflict(ValueError):
    pass


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        interview_session_id: UUID,
        event_type: str,
        payload: dict[str, object],
        deduplication_key: str,
        available_at: datetime,
        source_watermark: int | None = None,
    ) -> tuple[OutboxEvent, bool]:
        existing = await self.by_deduplication_key(deduplication_key)
        if existing is not None:
            if (
                existing.aggregate_type != aggregate_type
                or existing.aggregate_id != aggregate_id
                or existing.interview_session_id != interview_session_id
                or existing.event_type != event_type
                or existing.payload != payload
            ):
                raise OutboxIdempotencyConflict(
                    "Outbox deduplication key conflicts with an existing intention"
                )
            return existing, False
        event = OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            interview_session_id=interview_session_id,
            event_type=event_type,
            payload=payload,
            deduplication_key=deduplication_key,
            available_at=available_at,
            status="PENDING",
            attempt_count=0,
            source_watermark=source_watermark,
        )
        self._session.add(event)
        await self._session.flush()
        return event, True

    async def by_deduplication_key(self, key: str) -> OutboxEvent | None:
        value = await self._session.scalar(
            select(OutboxEvent).where(OutboxEvent.deduplication_key == key)
        )
        return cast(OutboxEvent | None, value)

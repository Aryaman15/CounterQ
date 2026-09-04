"""Persistence boundary for immutable, versioned CounterMap projections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.countermap.models import CounterMapProjection
from app.countermap.schema import COUNTERMAP_GENERATION_POLICY_VERSION, COUNTERMAP_SCHEMA_VERSION
from app.interviews.models import InterviewSession


class CounterMapProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_ready(self, session_id: UUID) -> CounterMapProjection | None:
        value = await self._session.scalar(
            select(CounterMapProjection).where(
                CounterMapProjection.interview_session_id == session_id,
                CounterMapProjection.status == "READY",
                CounterMapProjection.is_current.is_(True),
                CounterMapProjection.schema_version == COUNTERMAP_SCHEMA_VERSION,
                CounterMapProjection.generation_policy_version
                == COUNTERMAP_GENERATION_POLICY_VERSION,
            )
        )
        return cast(CounterMapProjection | None, value)

    async def latest(self, session_id: UUID) -> CounterMapProjection | None:
        value = await self._session.scalar(
            select(CounterMapProjection)
            .where(CounterMapProjection.interview_session_id == session_id)
            .order_by(CounterMapProjection.projection_version.desc())
            .limit(1)
        )
        return cast(CounterMapProjection | None, value)

    async def for_request(
        self,
        *,
        session_id: UUID,
        generation_request_key: str,
    ) -> CounterMapProjection | None:
        value = await self._session.scalar(
            select(CounterMapProjection).where(
                CounterMapProjection.interview_session_id == session_id,
                CounterMapProjection.generation_request_key == generation_request_key,
            )
        )
        return cast(CounterMapProjection | None, value)

    async def prepare_generation(
        self,
        *,
        session_id: UUID,
        generation_request_key: str,
        source_watermark: int,
        source_identity: str,
    ) -> tuple[CounterMapProjection, bool]:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update()
        )
        if interview is None or interview.status != "COMPLETED":
            raise ValueError("CounterMap requires a completed interview")
        existing = await self.for_request(
            session_id=session_id,
            generation_request_key=generation_request_key,
        )
        if existing is not None:
            identity_matches = (
                existing.source_identity == source_identity
                and existing.source_watermark == source_watermark
                and existing.schema_version == COUNTERMAP_SCHEMA_VERSION
                and existing.generation_policy_version
                == COUNTERMAP_GENERATION_POLICY_VERSION
            )
            if not identity_matches:
                existing.status = "STALE"
                existing.is_current = False
                existing.last_failure_category = "GENERATION_IDENTITY_MISMATCH"
                existing.updated_at = datetime.now(UTC)
                await self._session.flush()
                return existing, False
            if existing.status == "READY":
                return existing, False
            existing.status = "BUILDING"
            existing.schema_version = COUNTERMAP_SCHEMA_VERSION
            existing.generation_policy_version = COUNTERMAP_GENERATION_POLICY_VERSION
            existing.source_watermark = source_watermark
            existing.source_identity = source_identity
            existing.graph_json = None
            existing.generated_at = None
            existing.is_current = False
            existing.last_failure_category = None
            existing.updated_at = datetime.now(UTC)
            await self._session.flush()
            return existing, True
        next_version = (
            int(
                await self._session.scalar(
                    select(
                        func.coalesce(func.max(CounterMapProjection.projection_version), 0)
                    ).where(CounterMapProjection.interview_session_id == session_id)
                )
                or 0
            )
            + 1
        )
        projection = CounterMapProjection(
            interview_session_id=session_id,
            projection_version=next_version,
            schema_version=COUNTERMAP_SCHEMA_VERSION,
            generation_policy_version=COUNTERMAP_GENERATION_POLICY_VERSION,
            generation_request_key=generation_request_key,
            source_watermark=source_watermark,
            source_identity=source_identity,
            status="BUILDING",
            graph_json=None,
            generated_at=None,
            is_current=False,
        )
        self._session.add(projection)
        await self._session.flush()
        return projection, True

    async def mark_ready(
        self,
        *,
        projection: CounterMapProjection,
        graph_json: dict[str, object],
        generated_at: datetime,
    ) -> None:
        previous = list(
            await self._session.scalars(
                select(CounterMapProjection)
                .where(
                    CounterMapProjection.interview_session_id == projection.interview_session_id,
                    CounterMapProjection.is_current.is_(True),
                    CounterMapProjection.id != projection.id,
                )
                .with_for_update()
            )
        )
        for old in previous:
            old.status = "STALE"
            old.is_current = False
            old.updated_at = generated_at
        projection.graph_json = graph_json
        projection.status = "READY"
        projection.generated_at = generated_at
        projection.is_current = True
        projection.last_failure_category = None
        projection.updated_at = generated_at
        await self._session.flush()

    async def mark_failed(self, *, projection_id: UUID, category: str) -> None:
        projection = await self._session.scalar(
            select(CounterMapProjection)
            .where(CounterMapProjection.id == projection_id)
            .with_for_update()
        )
        if projection is None or projection.status == "READY":
            return
        projection.status = "FAILED"
        projection.graph_json = None
        projection.generated_at = None
        projection.is_current = False
        projection.last_failure_category = category[:128]
        projection.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_stale(self, projection_id: UUID) -> None:
        projection = await self._session.scalar(
            select(CounterMapProjection)
            .where(CounterMapProjection.id == projection_id)
            .with_for_update()
        )
        if projection is None:
            return
        was_ready = projection.status == "READY"
        projection.status = "STALE"
        if not was_ready:
            projection.graph_json = None
            projection.generated_at = None
        projection.is_current = False
        projection.last_failure_category = "SOURCE_CHANGED"
        projection.updated_at = datetime.now(UTC)
        await self._session.flush()

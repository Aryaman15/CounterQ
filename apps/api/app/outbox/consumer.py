"""Idempotent consumers for the bounded Stage 6B post-session chain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeGuard
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.interviews.models import InterviewSession
from app.outbox.claims import OutboxWorkClaim
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository
from app.reports.policy import SESSION_REPORT_POLICY_ID
from app.reports.service import (
    SessionReportGenerationError,
    SessionReportGenerationService,
    initial_report_generation_key,
)

logger = structlog.get_logger(__name__)
ConsumerStatus = Literal["COMPLETED", "RETRY", "FAILED", "SKIPPED"]


@dataclass(frozen=True)
class ConsumerResult:
    outbox_event_id: UUID
    status: ConsumerStatus
    category: str | None = None


class PostSessionOutboxConsumer:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        evidence_coordinator: SessionEvidenceEvaluationCoordinator,
        report_service: SessionReportGenerationService,
        max_attempts: int = 5,
        processing_lease_seconds: int = 120,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._evidence_coordinator = evidence_coordinator
        self._report_service = report_service
        self._max_attempts = max_attempts
        self._processing_lease_seconds = processing_lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def consume(self, outbox_event_id: UUID, attempt: int) -> ConsumerResult:
        event = await self._begin_processing(outbox_event_id, attempt)
        if event is None:
            return ConsumerResult(outbox_event_id, "SKIPPED", "OUTBOX_OWNERSHIP_LOST")
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(
                outbox_event_id=outbox_event_id,
                attempt=attempt,
                stop_event=stop_heartbeat,
            )
        )
        try:
            if event.event_type == "FINALIZE_SESSION_EVIDENCE":
                return await self._finalize_evidence(event, attempt)
            if event.event_type == "GENERATE_SESSION_REPORT":
                return await self._generate_report(event, attempt)
            return await self._record_failure(
                event.id,
                attempt,
                "UNSUPPORTED_OUTBOX_EVENT",
                permanent=True,
            )
        except SessionReportGenerationError as exc:
            return await self._record_failure(event.id, attempt, exc.category)
        except Exception as exc:
            return await self._record_failure(event.id, attempt, type(exc).__name__)
        finally:
            stop_heartbeat.set()
            await heartbeat

    async def _begin_processing(self, event_id: UUID, attempt: int) -> OutboxEvent | None:
        now = self._clock()
        async with self._sessionmaker() as session:
            async with session.begin():
                event = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
                )
                if event is None or event.attempt_count != attempt:
                    return None
                publication_acknowledged = event.status == "PUBLISHED"
                # Receipt of the exact reserved attempt is durable proof of publication.
                publication_reserved = event.status == "PROCESSING" and event.published_at is None
                if not publication_acknowledged and not publication_reserved:
                    return None
                event.status = "PROCESSING"
                event.published_at = event.published_at or now
                event.next_retry_at = now + timedelta(seconds=self._processing_lease_seconds)
                event.last_error = None
                await session.flush()
                session.expunge(event)
                return event

    async def _heartbeat(
        self,
        *,
        outbox_event_id: UUID,
        attempt: int,
        stop_event: asyncio.Event,
    ) -> None:
        interval = max(0.1, self._processing_lease_seconds / 3)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                if not await self._renew_lease(outbox_event_id, attempt):
                    return
            except Exception as exc:
                logger.warning(
                    "outbox_heartbeat_failed",
                    outbox_event_id=str(outbox_event_id),
                    attempt=attempt,
                    error_class=type(exc).__name__,
                )

    async def _renew_lease(self, event_id: UUID, attempt: int) -> bool:
        now = self._clock()
        async with self._sessionmaker() as session:
            async with session.begin():
                event = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
                )
                if not _owns_work_claim(event, attempt):
                    return False
                event.next_retry_at = now + timedelta(seconds=self._processing_lease_seconds)
                return True

    async def _finalize_evidence(self, event: OutboxEvent, attempt: int) -> ConsumerResult:
        result = await self._evidence_coordinator.evaluate(event.interview_session_id)
        if result.failed_units:
            categories = sorted(
                {
                    unit.error_category or "EVIDENCE_UNIT_FAILED"
                    for unit in result.units
                    if unit.status == "FAILED"
                }
            )
            return await self._record_failure(
                event.id,
                attempt,
                "EVIDENCE_FINALIZATION_FAILED:" + ",".join(categories),
            )
        now = self._clock()
        async with self._sessionmaker() as session:
            async with session.begin():
                current = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.id == event.id).with_for_update()
                )
                if not _owns_work_claim(current, attempt):
                    return ConsumerResult(event.id, "SKIPPED", "OUTBOX_OWNERSHIP_LOST")
                interview = await session.get(InterviewSession, event.interview_session_id)
                if interview is None:
                    current.status = "FAILED"
                    current.last_error = "SESSION_NOT_FOUND"
                    current.next_retry_at = None
                    return ConsumerResult(event.id, "FAILED", "SESSION_NOT_FOUND")
                request_key = initial_report_generation_key(event.interview_session_id)
                await OutboxRepository(session).enqueue(
                    aggregate_type="InterviewSession",
                    aggregate_id=event.interview_session_id,
                    interview_session_id=event.interview_session_id,
                    event_type="GENERATE_SESSION_REPORT",
                    payload={
                        "interview_session_id": str(event.interview_session_id),
                        "generation_request_key": request_key,
                        "report_policy": SESSION_REPORT_POLICY_ID,
                    },
                    deduplication_key=request_key,
                    available_at=now,
                    source_watermark=interview.last_server_sequence,
                )
                _mark_completed(current, now)
        return ConsumerResult(event.id, "COMPLETED")

    async def _generate_report(self, event: OutboxEvent, attempt: int) -> ConsumerResult:
        request_key = event.payload.get("generation_request_key")
        if not isinstance(request_key, str) or not request_key:
            return await self._record_failure(
                event.id,
                attempt,
                "INVALID_REPORT_REQUEST",
                permanent=True,
            )
        await self._report_service.generate(
            interview_session_id=event.interview_session_id,
            generation_request_key=request_key,
            work_claim=OutboxWorkClaim(event.id, attempt),
        )
        async with self._sessionmaker() as session:
            async with session.begin():
                current = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.id == event.id).with_for_update()
                )
                if not _owns_work_claim(current, attempt):
                    return ConsumerResult(event.id, "SKIPPED", "OUTBOX_OWNERSHIP_LOST")
                _mark_completed(current, self._clock())
        return ConsumerResult(event.id, "COMPLETED")

    async def _record_failure(
        self,
        event_id: UUID,
        attempt: int,
        category: str,
        *,
        permanent: bool = False,
    ) -> ConsumerResult:
        now = self._clock()
        async with self._sessionmaker() as session:
            async with session.begin():
                event = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
                )
                if not _owns_work_claim(event, attempt):
                    return ConsumerResult(event_id, "SKIPPED", "OUTBOX_OWNERSHIP_LOST")
                event.last_error = category[:2000]
                event.published_at = event.published_at
                if permanent or event.attempt_count >= self._max_attempts:
                    event.status = "FAILED"
                    event.next_retry_at = None
                    status: ConsumerStatus = "FAILED"
                else:
                    event.status = "RETRY"
                    event.next_retry_at = now + timedelta(
                        seconds=min(300, 2 ** min(event.attempt_count, 8))
                    )
                    status = "RETRY"
                logger.warning(
                    "outbox_consumer_failed",
                    outbox_event_id=str(event.id),
                    event_type=event.event_type,
                    attempt=event.attempt_count,
                    status=event.status,
                    error_category=category,
                )
        return ConsumerResult(event_id, status, category)


def _mark_completed(event: OutboxEvent, now: datetime) -> None:
    event.status = "COMPLETED"
    event.completed_at = now
    event.next_retry_at = None
    event.last_error = None


def _owns_work_claim(
    event: OutboxEvent | None,
    attempt: int,
) -> TypeGuard[OutboxEvent]:
    return bool(
        event is not None
        and event.attempt_count == attempt
        and event.status == "PROCESSING"
    )

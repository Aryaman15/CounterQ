"""Idempotent consumers for the bounded post-session derived-work chain."""

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

from app.countermap.schema import COUNTERMAP_GENERATION_POLICY_VERSION
from app.countermap.service import (
    CounterMapGenerationError,
    CounterMapGenerationService,
    initial_countermap_generation_key,
)
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.interviews.deletion import InterviewDeletionCleanupService
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.mastery.policy import MASTERY_POLICY_VERSION
from app.mastery.service import (
    MasteryRecalculationError,
    MasteryRecalculationService,
    initial_mastery_recalculation_key,
)
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
        countermap_service: CounterMapGenerationService | None = None,
        mastery_service: MasteryRecalculationService | None = None,
        deletion_service: InterviewDeletionCleanupService | None = None,
        max_attempts: int = 5,
        processing_lease_seconds: int = 120,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._evidence_coordinator = evidence_coordinator
        self._report_service = report_service
        self._countermap_service = countermap_service
        self._mastery_service = mastery_service
        self._deletion_service = deletion_service
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
            if event.event_type == "GENERATE_COUNTERMAP":
                return await self._generate_countermap(event, attempt)
            if event.event_type == "RECALCULATE_MASTERY":
                return await self._recalculate_mastery(event, attempt)
            if event.event_type == "DELETE_INTERVIEW":
                return await self._delete_interview(event, attempt)
            return await self._record_failure(
                event.id,
                attempt,
                "UNSUPPORTED_OUTBOX_EVENT",
                permanent=True,
            )
        except (
            SessionReportGenerationError,
            CounterMapGenerationError,
            MasteryRecalculationError,
        ) as exc:
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
                interview = await session.get(InterviewSession, event.interview_session_id)
                deletion_eligible = (
                    event.event_type == "DELETE_INTERVIEW"
                    and interview is not None
                    and interview.status == "DELETION_PENDING"
                )
                ordinary_eligible = (
                    event.event_type != "DELETE_INTERVIEW"
                    and interview is not None
                    and interview.status != "DELETION_PENDING"
                )
                if not deletion_eligible and not ordinary_eligible:
                    event.status = "FAILED"
                    event.last_error = (
                        "SESSION_DELETION_PENDING"
                        if interview is not None and interview.status == "DELETION_PENDING"
                        else "SESSION_NOT_FOUND"
                    )
                    event.next_retry_at = None
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
                if interview.status == "DELETION_PENDING":
                    current.status = "FAILED"
                    current.last_error = "SESSION_DELETION_PENDING"
                    current.next_retry_at = None
                    return ConsumerResult(event.id, "FAILED", "SESSION_DELETION_PENDING")
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
                countermap_key = initial_countermap_generation_key(event.interview_session_id)
                await OutboxRepository(session).enqueue(
                    aggregate_type="InterviewSession",
                    aggregate_id=event.interview_session_id,
                    interview_session_id=event.interview_session_id,
                    event_type="GENERATE_COUNTERMAP",
                    payload={
                        "interview_session_id": str(event.interview_session_id),
                        "generation_request_key": countermap_key,
                        "generation_policy": COUNTERMAP_GENERATION_POLICY_VERSION,
                    },
                    deduplication_key=countermap_key,
                    available_at=now,
                    source_watermark=interview.last_server_sequence,
                )
                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                if configuration is None:
                    current.status = "FAILED"
                    current.last_error = "INTERVIEW_CONFIGURATION_NOT_FOUND"
                    current.next_retry_at = None
                    return ConsumerResult(event.id, "FAILED", "INTERVIEW_CONFIGURATION_NOT_FOUND")
                mastery_key = initial_mastery_recalculation_key(
                    interview.user_id, event.interview_session_id
                )
                await OutboxRepository(session).enqueue(
                    aggregate_type="User",
                    aggregate_id=interview.user_id,
                    interview_session_id=event.interview_session_id,
                    event_type="RECALCULATE_MASTERY",
                    payload={
                        "user_id": str(interview.user_id),
                        "source_interview_session_id": str(event.interview_session_id),
                        "source_session_level": configuration.level,
                        "mastery_policy_version": MASTERY_POLICY_VERSION,
                    },
                    deduplication_key=mastery_key,
                    available_at=now,
                    source_watermark=interview.last_server_sequence,
                )
                _mark_completed(current, now)
        return ConsumerResult(event.id, "COMPLETED")

    async def _delete_interview(
        self,
        event: OutboxEvent,
        attempt: int,
    ) -> ConsumerResult:
        policy_version = event.payload.get("deletion_policy_version")
        requested_session_id = event.payload.get("interview_session_id")
        if (
            policy_version != "interview-deletion.v1"
            or requested_session_id != str(event.interview_session_id)
        ):
            return await self._record_failure(
                event.id, attempt, "INVALID_DELETION_REQUEST", permanent=True
            )
        if self._deletion_service is None:
            return await self._record_failure(
                event.id, attempt, "DELETION_SERVICE_UNAVAILABLE", permanent=True
            )
        result = await self._deletion_service.delete(
            outbox_event_id=event.id,
            attempt=attempt,
        )
        if result is None:
            return ConsumerResult(event.id, "SKIPPED", "OUTBOX_OWNERSHIP_LOST")
        # The deletion transaction removes this outbox row with the session graph.
        return ConsumerResult(event.id, "COMPLETED")

    async def _recalculate_mastery(
        self,
        event: OutboxEvent,
        attempt: int,
    ) -> ConsumerResult:
        user_id = event.payload.get("user_id")
        policy_version = event.payload.get("mastery_policy_version")
        if (
            not isinstance(user_id, str)
            or policy_version != MASTERY_POLICY_VERSION
        ):
            return await self._record_failure(
                event.id, attempt, "INVALID_MASTERY_REQUEST", permanent=True
            )
        if self._mastery_service is None:
            return await self._record_failure(
                event.id, attempt, "MASTERY_SERVICE_UNAVAILABLE", permanent=True
            )
        try:
            parsed_user_id = UUID(user_id)
        except ValueError:
            return await self._record_failure(
                event.id, attempt, "INVALID_MASTERY_REQUEST", permanent=True
            )
        await self._mastery_service.recalculate(
            user_id=parsed_user_id,
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

    async def _generate_countermap(
        self,
        event: OutboxEvent,
        attempt: int,
    ) -> ConsumerResult:
        request_key = event.payload.get("generation_request_key")
        if not isinstance(request_key, str) or not request_key:
            return await self._record_failure(
                event.id,
                attempt,
                "INVALID_COUNTERMAP_REQUEST",
                permanent=True,
            )
        if self._countermap_service is None:
            return await self._record_failure(
                event.id,
                attempt,
                "COUNTERMAP_SERVICE_UNAVAILABLE",
                permanent=True,
            )
        await self._countermap_service.generate(
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
        event is not None and event.attempt_count == attempt and event.status == "PROCESSING"
    )

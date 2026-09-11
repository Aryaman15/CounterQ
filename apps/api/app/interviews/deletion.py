"""Privacy deletion with deterministic learning-model repair."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeGuard
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evidence.breakpoints import BreakpointService
from app.evidence.models import BreakpointEvidence, Evidence
from app.interviews.authorization import InterviewOwnershipRepository
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.mastery.models import RetestAttempt
from app.mastery.service import MasteryRecalculationService
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository

DELETION_POLICY_VERSION = "interview-deletion.v1"
DELETION_INVALIDATION_REASON = "SOURCE_INTERVIEW_DELETED"
DELETION_SUPERSESSION_REASON = "SESSION_DELETION_PENDING"


def interview_deletion_key(interview_session_id: UUID) -> str:
    return f"interview-deletion:{interview_session_id}:{DELETION_POLICY_VERSION}"


@dataclass(frozen=True, slots=True)
class InterviewDeletionRequest:
    interview_session_id: UUID
    deletion_request_id: UUID


@dataclass(frozen=True, slots=True)
class InterviewDeletionResult:
    interview_session_id: UUID
    user_id: UUID
    invalidated_evidence_count: int
    recalculated_breakpoint_count: int


class InterviewDeletionRequestService:
    """Persist deletion intent and make the interview unavailable atomically."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def request(
        self,
        *,
        principal_user_id: UUID,
        interview_session_id: UUID,
    ) -> InterviewDeletionRequest:
        interview = await InterviewOwnershipRepository(self._session).get_owned(
            principal_user_id=principal_user_id,
            interview_session_id=interview_session_id,
            for_update=True,
        )
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Interview deletion clock must be timezone-aware")

        if interview.status != "DELETION_PENDING":
            interview.status = "DELETION_PENDING"
            interview.state_version += 1
            active_events = list(
                await self._session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.interview_session_id == interview.id,
                        OutboxEvent.event_type != "DELETE_INTERVIEW",
                        OutboxEvent.status.in_(
                            ("PENDING", "PUBLISHED", "PROCESSING", "RETRY")
                        ),
                    )
                    .with_for_update()
                )
            )
            for event in active_events:
                event.status = "FAILED"
                event.next_retry_at = None
                event.last_error = DELETION_SUPERSESSION_REASON

        deletion_event, _created = await OutboxRepository(self._session).enqueue(
            aggregate_type="InterviewSession",
            aggregate_id=interview.id,
            interview_session_id=interview.id,
            event_type="DELETE_INTERVIEW",
            payload={
                "interview_session_id": str(interview.id),
                "deletion_policy_version": DELETION_POLICY_VERSION,
            },
            deduplication_key=interview_deletion_key(interview.id),
            available_at=now,
            source_watermark=interview.last_server_sequence,
        )
        if deletion_event.status == "FAILED":
            deletion_event.status = "RETRY"
            deletion_event.next_retry_at = max(now, deletion_event.created_at)
            deletion_event.last_error = None
        await self._session.flush()
        return InterviewDeletionRequest(interview.id, deletion_event.id)


class InterviewDeletionCleanupService:
    """Hard-delete one interview and repair all user-wide derived learning state."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        mastery_service: MasteryRecalculationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._mastery_service = mastery_service
        self._clock = clock or (lambda: datetime.now(UTC))

    async def delete(
        self,
        *,
        outbox_event_id: UUID,
        attempt: int,
    ) -> InterviewDeletionResult | None:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Interview deletion clock must be timezone-aware")
        async with self._sessionmaker() as session, session.begin():
            event = await session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.id == outbox_event_id)
                .with_for_update()
            )
            if not _owns_deletion_claim(event, attempt):
                return None
            interview = await session.scalar(
                select(InterviewSession)
                .where(InterviewSession.id == event.interview_session_id)
                .with_for_update()
            )
            if interview is None or interview.status != "DELETION_PENDING":
                return None

            session_id = interview.id
            user_id = interview.user_id
            configuration_id = interview.interview_configuration_id
            evidence_rows = list(
                await session.scalars(
                    select(Evidence)
                    .where(Evidence.interview_session_id == session_id)
                    .with_for_update()
                )
            )
            for evidence in evidence_rows:
                evidence.validation_status = "INVALIDATED"
                evidence.invalidated_at = now
                evidence.invalidation_reason = DELETION_INVALIDATION_REASON
            await session.flush()

            recalculated_breakpoints: set[UUID] = set()
            breakpoint_service = BreakpointService(session)
            for evidence in evidence_rows:
                recalculated_breakpoints.update(
                    await breakpoint_service.recalculate_support_for_evidence(
                        evidence.id,
                        recalculated_at=now,
                        invalidation_reason=DELETION_INVALIDATION_REASON,
                    )
                )

            evidence_ids = [item.id for item in evidence_rows]
            if evidence_ids:
                await session.execute(
                    delete(BreakpointEvidence).where(
                        BreakpointEvidence.evidence_id.in_(evidence_ids)
                    )
                )
            await session.execute(
                delete(RetestAttempt).where(
                    RetestAttempt.interview_session_id == session_id
                )
            )

            # The canonical Stage 8 service and policy own every Mastery/retest rewrite.
            await self._mastery_service.recalculate_in_transaction(
                session,
                user_id=user_id,
            )

            # Database cascades remove the full session-owned graph. AI invocation
            # accounting rows deliberately survive with their session FK set null.
            await session.execute(
                delete(InterviewSession).where(InterviewSession.id == session_id)
            )
            await session.execute(
                delete(InterviewConfiguration).where(
                    InterviewConfiguration.id == configuration_id
                )
            )
            return InterviewDeletionResult(
                interview_session_id=session_id,
                user_id=user_id,
                invalidated_evidence_count=len(evidence_rows),
                recalculated_breakpoint_count=len(recalculated_breakpoints),
            )


def _owns_deletion_claim(
    event: OutboxEvent | None,
    attempt: int,
) -> TypeGuard[OutboxEvent]:
    return bool(
        event is not None
        and event.event_type == "DELETE_INTERVIEW"
        and event.attempt_count == attempt
        and event.status == "PROCESSING"
    )

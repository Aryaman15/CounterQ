from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.floor import ConversationFloor
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery, InterviewSession
from app.interviews.prompt_policy import (
    ensure_no_active_delivery,
    validate_prompt_delivery_eligibility,
)
from app.interviews.runtime import AcceptEventCommand, IdempotencyConflict, InterviewRuntime
from app.observation.models import TranscriptSegment
from app.observation.repository import ObservationRepository
from app.realtime.control_protocol import (
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
    RealtimeDisconnectedMessage,
    RealtimeReconnectedMessage,
)

DEVELOPMENT_DELIVERY_VALIDATION_TEXT = "Walk me through the approach you're considering."
DEVELOPMENT_DELIVERY_VALIDATION_INTENT = "stage1_realtime_delivery_validation"


class RealtimeControlError(ValueError):
    pass


class RealtimeControlSessionNotFound(RealtimeControlError):
    pass


class RealtimeControlConflict(RealtimeControlError):
    pass


@dataclass(frozen=True)
class TranscriptPersistenceResult:
    event_id: UUID
    transcript_segment_id: UUID
    server_sequence: int
    interview_state_version: int
    created: bool


@dataclass(frozen=True)
class ConnectivityPersistenceResult:
    event_id: UUID
    server_sequence: int
    interview_state_version: int
    created: bool


@dataclass(frozen=True)
class DevelopmentPromptResult:
    prompt_id: UUID
    text: str


@dataclass(frozen=True)
class DeliveryPersistenceResult:
    prompt_id: UUID
    delivery_id: UUID
    delivery_state: str
    interview_state_version: int
    created: bool
    event_id: UUID | None = None
    transcript_segment_id: UUID | None = None
    server_sequence: int | None = None


class RealtimeControlService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self.floor = ConversationFloor()

    async def ensure_session_exists(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.get(InterviewSession, session_id)
        if interview is None:
            raise RealtimeControlSessionNotFound(f"InterviewSession not found: {session_id}")
        return interview

    async def persist_candidate_transcript(
        self,
        *,
        session_id: UUID,
        message: CandidateTranscriptFinalizedMessage,
    ) -> TranscriptPersistenceResult:
        interview = await self.ensure_session_exists(session_id)
        provider_segment_id = message.provider_item_id
        idempotency_key = message.idempotency_key or f"candidate-transcript:{provider_segment_id}"
        occurred_at = message.ended_at or self._clock()
        payload: dict[str, object] = {"provider_segment_id": provider_segment_id}
        if message.content_index is not None:
            payload["content_index"] = message.content_index

        accepted = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type="TRANSCRIPT_FINALIZED",
                source="CANDIDATE_VOICE",
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                payload=payload,
                provenance={
                    "realtime_provider_item_id": provider_segment_id,
                    "content_index": message.content_index,
                },
                schema_version="transcript.final.v1",
                client_instance_id=message.client_instance_id,
                client_sequence=message.client_sequence,
            ),
        )
        if not accepted.created:
            segment = await self._segment_for_event(accepted.event.id)
            if segment is None:
                raise RealtimeControlConflict(
                    "Idempotent transcript event exists without TranscriptSegment"
                )
            if (
                segment.text != message.transcript
                or segment.provider_segment_id != provider_segment_id
                or segment.speaker != "CANDIDATE"
            ):
                raise IdempotencyConflict(
                    "Candidate transcript idempotency key conflicts with existing transcript"
                )
            return TranscriptPersistenceResult(
                event_id=accepted.event.id,
                transcript_segment_id=segment.id,
                server_sequence=accepted.event.server_sequence,
                interview_state_version=accepted.event.interview_state_version,
                created=False,
            )

        segment = await ObservationRepository(self._session).add_transcript_segment(
            session_id=session_id,
            event_id=accepted.event.id,
            speaker="CANDIDATE",
            sequence=accepted.event.server_sequence,
            started_at=message.started_at or occurred_at,
            ended_at=message.ended_at,
            text=message.transcript,
            interview_stage=interview.current_stage,
            interview_state_version=interview.state_version,
            provider_segment_id=provider_segment_id,
        )
        return TranscriptPersistenceResult(
            event_id=accepted.event.id,
            transcript_segment_id=segment.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=True,
        )

    async def persist_realtime_connectivity_event(
        self,
        *,
        session_id: UUID,
        message: RealtimeDisconnectedMessage | RealtimeReconnectedMessage,
    ) -> ConnectivityPersistenceResult:
        event_type = (
            "REALTIME_DISCONNECTED"
            if message.type == "realtime_disconnected"
            else "REALTIME_RECONNECTED"
        )
        idempotency_key = message.idempotency_key or (
            f"{message.type}:{message.provider_session_id or message.client_event_id}"
        )
        payload: dict[str, object] = {}
        if message.provider_session_id is not None:
            payload["provider_session_id"] = message.provider_session_id
        if isinstance(message, RealtimeDisconnectedMessage) and message.reason is not None:
            payload["reason"] = message.reason
        accepted = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type=event_type,
                source="SYSTEM",
                occurred_at=message.occurred_at or self._clock(),
                idempotency_key=idempotency_key,
                payload=payload,
                provenance={"client_event_id": message.client_event_id},
                schema_version="realtime.connectivity.v1",
                client_instance_id=message.client_instance_id,
                client_sequence=message.client_sequence,
            ),
        )
        return ConnectivityPersistenceResult(
            event_id=accepted.event.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=accepted.created,
        )

    def candidate_speech_started(self) -> ConversationFloor:
        self.floor = self.floor.candidate_speech_started()
        return self.floor

    def candidate_speech_stopped(self) -> ConversationFloor:
        self.floor = self.floor.candidate_paused()
        return self.floor

    async def create_development_authorized_prompt(
        self,
        *,
        session_id: UUID,
    ) -> DevelopmentPromptResult:
        await self.ensure_session_exists(session_id)
        prompt = await InterviewInteractionRepository(self._session).add_prompt(
            interview_session_id=session_id,
            origin="SYSTEM",
            kind="INSTRUCTION",
            intent=DEVELOPMENT_DELIVERY_VALIDATION_INTENT,
            status="AUTHORIZED",
            authorized_at=self._clock(),
        )
        return DevelopmentPromptResult(
            prompt_id=prompt.id,
            text=DEVELOPMENT_DELIVERY_VALIDATION_TEXT,
        )

    async def start_delivery(
        self,
        *,
        session_id: UUID,
        message: CounterQDeliveryStartedMessage,
    ) -> DeliveryPersistenceResult:
        prompt = await self._prompt_for_session(session_id, message.interviewer_prompt_id)
        existing = await self._delivery_for_provider_response(
            session_id=session_id,
            prompt_id=message.interviewer_prompt_id,
            provider_response_id=message.provider_response_id,
        )
        if existing is not None:
            interview = await self.ensure_session_exists(session_id)
            return DeliveryPersistenceResult(
                prompt_id=prompt.id,
                delivery_id=existing.id,
                delivery_state=existing.delivery_state,
                interview_state_version=interview.state_version,
                created=False,
            )

        examiner_decision = (
            prompt.examiner_decision if prompt.origin == "EXAMINER_DECISION" else None
        )
        validate_prompt_delivery_eligibility(prompt=prompt, examiner_decision=examiner_decision)
        await ensure_no_active_delivery(self._session, session_id)
        delivery_attempt = await self._next_delivery_attempt(prompt.id)
        delivery = await InterviewInteractionRepository(self._session).add_delivery(
            interview_session_id=session_id,
            interviewer_prompt_id=prompt.id,
            delivery_attempt=delivery_attempt,
            intended_text=message.intended_text,
            delivery_state="STARTED",
            started_at=message.started_at or self._clock(),
            realtime_provider_event_id=message.provider_response_id,
        )
        floor = self.floor.try_counterq_speaking(str(delivery.id))
        if floor is not None:
            self.floor = floor
        return DeliveryPersistenceResult(
            prompt_id=prompt.id,
            delivery_id=delivery.id,
            delivery_state=delivery.delivery_state,
            interview_state_version=(await self.ensure_session_exists(session_id)).state_version,
            created=True,
        )

    async def complete_delivery(
        self,
        *,
        session_id: UUID,
        message: CounterQDeliveryCompletedMessage,
    ) -> DeliveryPersistenceResult:
        interview = await self.ensure_session_exists(session_id)
        prompt = await self._prompt_for_session(session_id, message.interviewer_prompt_id)
        delivery = await self._delivery_for_session(session_id, message.prompt_delivery_id)
        idempotency_key = message.idempotency_key or (
            f"counterq-delivered:{delivery.id}:{message.provider_response_id}"
        )
        payload: dict[str, object] = {
            "interviewer_prompt_id": str(prompt.id),
            "prompt_delivery_id": str(delivery.id),
            "provider_response_id": message.provider_response_id,
        }
        if message.provider_item_id is not None:
            payload["provider_item_id"] = message.provider_item_id
        accepted = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type="COUNTERQ_UTTERANCE_DELIVERED",
                source="COUNTERQ_VOICE",
                occurred_at=message.completed_at or self._clock(),
                idempotency_key=idempotency_key,
                payload=payload,
                provenance={
                    "provider_response_id": message.provider_response_id,
                    "provider_item_id": message.provider_item_id,
                },
                schema_version="counterq.delivery.completed.v1",
                client_instance_id=message.client_instance_id,
                client_sequence=message.client_sequence,
            ),
        )
        segment = await self._segment_for_event(accepted.event.id)
        if segment is None:
            segment = await ObservationRepository(self._session).add_transcript_segment(
                session_id=session_id,
                event_id=accepted.event.id,
                speaker="COUNTERQ",
                sequence=accepted.event.server_sequence,
                started_at=delivery.started_at,
                ended_at=message.completed_at or self._clock(),
                text=message.transcript,
                interview_stage=interview.current_stage,
                interview_state_version=interview.state_version,
                delivery_state="DELIVERED",
                provider_segment_id=message.provider_item_id or message.provider_response_id,
            )
        elif segment.text != message.transcript or segment.speaker != "COUNTERQ":
            raise IdempotencyConflict(
                "CounterQ delivery idempotency key conflicts with existing transcript"
            )

        delivery.actual_transcript_segment_id = segment.id
        delivery.delivery_state = "DELIVERED"
        delivery.completed_at = message.completed_at or self._clock()
        prompt.status = "DELIVERED"
        self.floor = self.floor.release()
        await self._session.flush()
        return DeliveryPersistenceResult(
            prompt_id=prompt.id,
            delivery_id=delivery.id,
            delivery_state=delivery.delivery_state,
            event_id=accepted.event.id,
            transcript_segment_id=segment.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=accepted.created,
        )

    async def interrupt_delivery(
        self,
        *,
        session_id: UUID,
        message: CounterQDeliveryInterruptedMessage,
    ) -> DeliveryPersistenceResult:
        prompt = await self._prompt_for_session(session_id, message.interviewer_prompt_id)
        delivery = await self._delivery_for_session(session_id, message.prompt_delivery_id)
        idempotency_key = message.idempotency_key or (
            f"counterq-interrupted:{delivery.id}:{message.provider_response_id}"
        )
        payload: dict[str, object] = {
            "interviewer_prompt_id": str(prompt.id),
            "prompt_delivery_id": str(delivery.id),
            "provider_response_id": message.provider_response_id,
            "confirmed_by": message.confirmed_by,
        }
        if message.provider_item_id is not None:
            payload["provider_item_id"] = message.provider_item_id
        if message.audio_end_ms is not None:
            payload["audio_end_ms"] = message.audio_end_ms
        accepted = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type="CANDIDATE_INTERRUPTED_COUNTERQ",
                source="CANDIDATE_VOICE",
                occurred_at=message.interrupted_at or self._clock(),
                idempotency_key=idempotency_key,
                payload=payload,
                provenance={
                    "provider_response_id": message.provider_response_id,
                    "provider_item_id": message.provider_item_id,
                    "confirmed_by": message.confirmed_by,
                },
                schema_version="counterq.delivery.interrupted.v1",
                client_instance_id=message.client_instance_id,
                client_sequence=message.client_sequence,
            ),
        )
        delivery.delivery_state = "INTERRUPTED"
        delivery.interrupted_at = message.interrupted_at or self._clock()
        prompt.status = "INTERRUPTED"
        self.floor = self.floor.candidate_speech_started()
        await self._session.flush()
        return DeliveryPersistenceResult(
            prompt_id=prompt.id,
            delivery_id=delivery.id,
            delivery_state=delivery.delivery_state,
            event_id=accepted.event.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=accepted.created,
        )

    async def _prompt_for_session(self, session_id: UUID, prompt_id: UUID) -> InterviewerPrompt:
        prompt = await self._session.scalar(
            select(InterviewerPrompt)
            .where(InterviewerPrompt.interview_session_id == session_id)
            .where(InterviewerPrompt.id == prompt_id),
        )
        if prompt is None:
            raise RealtimeControlSessionNotFound("InterviewerPrompt not found for session")
        return prompt

    async def _delivery_for_session(
        self,
        session_id: UUID,
        delivery_id: UUID,
    ) -> InterviewerPromptDelivery:
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(InterviewerPromptDelivery.interview_session_id == session_id)
            .where(InterviewerPromptDelivery.id == delivery_id),
        )
        if delivery is None:
            raise RealtimeControlSessionNotFound("InterviewerPromptDelivery not found for session")
        return delivery

    async def _delivery_for_provider_response(
        self,
        *,
        session_id: UUID,
        prompt_id: UUID,
        provider_response_id: str,
    ) -> InterviewerPromptDelivery | None:
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(InterviewerPromptDelivery.interview_session_id == session_id)
            .where(InterviewerPromptDelivery.interviewer_prompt_id == prompt_id)
            .where(
                InterviewerPromptDelivery.realtime_provider_event_id == provider_response_id
            ),
        )
        return cast(InterviewerPromptDelivery | None, delivery)

    async def _segment_for_event(self, event_id: UUID) -> TranscriptSegment | None:
        segment = await self._session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.interview_event_id == event_id),
        )
        return cast(TranscriptSegment | None, segment)

    async def _next_delivery_attempt(self, prompt_id: UUID) -> int:
        max_attempt = await self._session.scalar(
            select(func.max(InterviewerPromptDelivery.delivery_attempt)).where(
                InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
            ),
        )
        return int(max_attempt or 0) + 1

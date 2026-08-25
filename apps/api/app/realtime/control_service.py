from __future__ import annotations

import difflib
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.interviews.floor import ConversationFloor
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    SessionBudget,
)
from app.interviews.prompt_authorization import (
    PromptAuthorizationService,
    PromptDeliveryPermit,
    PromptGateResult,
    PromptGateRuntimeState,
)
from app.interviews.prompt_policy import (
    ensure_no_active_delivery,
    validate_prompt_delivery_eligibility,
)
from app.interviews.runtime import AcceptEventCommand, IdempotencyConflict, InterviewRuntime
from app.observation.engine import ObservationEngine, StructuredObservation
from app.observation.models import CodeDiff, InterviewEvent, TranscriptSegment
from app.observation.repository import ObservationRepository
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
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
    observation: StructuredObservation | None = None


@dataclass(frozen=True)
class ConnectivityPersistenceResult:
    event_id: UUID
    server_sequence: int
    interview_state_version: int
    created: bool


@dataclass(frozen=True)
class CodeSnapshotPersistenceResult:
    snapshot_id: UUID
    version_number: int
    content_hash: str
    interview_state_version: int
    created: bool
    event_id: UUID | None = None
    diff_id: UUID | None = None
    server_sequence: int | None = None
    observation: StructuredObservation | None = None


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
    observation: StructuredObservation | None = None


@dataclass(frozen=True)
class RealtimeControlRuntimeState:
    candidate_speaking: bool = False
    candidate_code_active: bool = False

    def prompt_gate_state(self) -> PromptGateRuntimeState:
        return PromptGateRuntimeState(
            candidate_speaking=self.candidate_speaking,
            candidate_code_active=self.candidate_code_active,
        )


class RealtimeControlService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
        authorized_prompt_delivery_window_seconds: float = 12.0,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self._authorized_prompt_delivery_window_seconds = (
            authorized_prompt_delivery_window_seconds
        )
        self.floor = ConversationFloor()

    async def ensure_session_exists(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.get(InterviewSession, session_id)
        if interview is None:
            raise RealtimeControlSessionNotFound(f"InterviewSession not found: {session_id}")
        return interview

    async def session_budget(self, session_id: UUID) -> SessionBudget:
        budget = await self._session.get(SessionBudget, session_id)
        if budget is None:
            raise RealtimeControlSessionNotFound(f"SessionBudget not found: {session_id}")
        return budget

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
                observation=await ObservationEngine(self._session).project_event(accepted.event.id),
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
            observation=await ObservationEngine(self._session).project_event(accepted.event.id),
        )

    async def persist_candidate_code_snapshot(
        self,
        *,
        session_id: UUID,
        message: CandidateCodeSnapshotMessage,
    ) -> CodeSnapshotPersistenceResult:
        interview = await self._lock_interview(session_id)
        normalized_source = normalize_source_code(message.source_code)
        content_hash = content_sha256(normalized_source)
        idempotency_key = message.idempotency_key or (
            f"candidate-code:{message.trigger}:{message.client_event_id}"
        )
        existing_event = await self._event_for_idempotency(session_id, idempotency_key)
        if existing_event is not None:
            return await self._code_result_for_idempotent_event(
                event=existing_event,
                expected_content_hash=content_hash,
                expected_language=message.language,
                expected_trigger=message.trigger,
            )

        observations = ObservationRepository(self._session)
        latest_snapshot = await observations.latest_code_snapshot(session_id)
        if latest_snapshot is not None and latest_snapshot.content_hash == content_hash:
            event = await self._session.get(InterviewEvent, latest_snapshot.created_from_event_id)
            return CodeSnapshotPersistenceResult(
                snapshot_id=latest_snapshot.id,
                version_number=latest_snapshot.version_number,
                content_hash=latest_snapshot.content_hash,
                interview_state_version=interview.state_version,
                created=False,
                event_id=event.id if event else None,
                server_sequence=event.server_sequence if event else None,
            )

        version_number = (latest_snapshot.version_number if latest_snapshot else 0) + 1
        event_type = (
            "CODE_SNAPSHOT_CREATED" if latest_snapshot is None else "MEANINGFUL_CODE_CHANGE"
        )
        payload: dict[str, object] = {
            "language": message.language,
            "trigger": message.trigger,
            "content_hash": content_hash,
            "version_number": version_number,
            "interview_stage": interview.current_stage,
        }
        if latest_snapshot is not None:
            payload["parent_snapshot_id"] = str(latest_snapshot.id)
            payload["parent_version_number"] = latest_snapshot.version_number

        accepted = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=session_id,
                event_type=event_type,
                source="NATIVE_EDITOR",
                occurred_at=message.occurred_at or self._clock(),
                idempotency_key=idempotency_key,
                payload=payload,
                provenance={"client_event_id": message.client_event_id},
                schema_version="code.snapshot.v1",
                client_instance_id=message.client_instance_id,
                client_sequence=message.client_sequence,
            ),
        )
        snapshot = await observations.add_code_snapshot(
            session_id=session_id,
            version_number=version_number,
            language=message.language,
            source_code=normalized_source,
            content_hash=content_hash,
            created_from_event_id=accepted.event.id,
            parent_snapshot_id=latest_snapshot.id if latest_snapshot else None,
        )
        accepted.event.code_snapshot_id = snapshot.id
        diff: CodeDiff | None = None
        if latest_snapshot is not None:
            diff = await observations.add_code_diff(
                session_id=session_id,
                from_snapshot_id=latest_snapshot.id,
                to_snapshot_id=snapshot.id,
                diff_format="unified",
                diff_content=unified_diff(
                    from_source=latest_snapshot.source_code,
                    to_source=normalized_source,
                    from_label=f"v{latest_snapshot.version_number}",
                    to_label=f"v{version_number}",
                ),
                change_summary=None,
                significance=None,
                created_from_event_id=accepted.event.id,
            )
        await self._session.flush()
        observation = await ObservationEngine(self._session).project_event(accepted.event.id)
        return CodeSnapshotPersistenceResult(
            snapshot_id=snapshot.id,
            version_number=snapshot.version_number,
            content_hash=snapshot.content_hash,
            interview_state_version=accepted.event.interview_state_version,
            created=True,
            event_id=accepted.event.id,
            diff_id=diff.id if diff else None,
            server_sequence=accepted.event.server_sequence,
            observation=observation,
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
        await InterviewRuntime(self._session, clock=self._clock).ensure_activity_allowed(session_id)
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

    async def evaluate_examiner_decision(
        self,
        *,
        session_id: UUID,
        decision_id: UUID,
        runtime_state: RealtimeControlRuntimeState | None = None,
    ) -> PromptGateResult:
        return await PromptAuthorizationService(
            self._session,
            clock=self._clock,
            authorized_prompt_delivery_window_seconds=(
                self._authorized_prompt_delivery_window_seconds
            ),
        ).evaluate_examiner_decision(
            session_id=session_id,
            decision_id=decision_id,
            runtime_state=(runtime_state or RealtimeControlRuntimeState()).prompt_gate_state(),
        )

    async def permit_prompt_delivery(
        self,
        *,
        session_id: UUID,
        prompt_id: UUID,
        runtime_state: RealtimeControlRuntimeState | None = None,
    ) -> PromptDeliveryPermit:
        return await PromptAuthorizationService(
            self._session,
            clock=self._clock,
            authorized_prompt_delivery_window_seconds=(
                self._authorized_prompt_delivery_window_seconds
            ),
        ).permit_delivery(
            session_id=session_id,
            prompt_id=prompt_id,
            runtime_state=(runtime_state or RealtimeControlRuntimeState()).prompt_gate_state(),
        )

    async def start_delivery(
        self,
        *,
        session_id: UUID,
        message: CounterQDeliveryStartedMessage,
    ) -> DeliveryPersistenceResult:
        await InterviewRuntime(self._session, clock=self._clock).ensure_activity_allowed(session_id)
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
        intended_text = (
            prompt.intent if prompt.origin == "EXAMINER_DECISION" else message.intended_text
        )
        delivery = await InterviewInteractionRepository(self._session).add_delivery(
            interview_session_id=session_id,
            interviewer_prompt_id=prompt.id,
            delivery_attempt=delivery_attempt,
            intended_text=intended_text,
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
        await PromptAuthorizationService(
            self._session,
            clock=self._clock,
            authorized_prompt_delivery_window_seconds=(
                self._authorized_prompt_delivery_window_seconds
            ),
        ).consume_probe_budget_for_delivered_prompt(prompt)
        prompt.status = "DELIVERED"
        self.floor = self.floor.release()
        await self._session.flush()
        observation = await ObservationEngine(self._session).project_event(accepted.event.id)
        return DeliveryPersistenceResult(
            prompt_id=prompt.id,
            delivery_id=delivery.id,
            delivery_state=delivery.delivery_state,
            event_id=accepted.event.id,
            transcript_segment_id=segment.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=accepted.created,
            observation=observation,
        )

    async def interrupt_delivery(
        self,
        *,
        session_id: UUID,
        message: CounterQDeliveryInterruptedMessage,
    ) -> DeliveryPersistenceResult:
        interview = await self.ensure_session_exists(session_id)
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
            "interview_stage": interview.current_stage,
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
        observation = await ObservationEngine(self._session).project_event(accepted.event.id)
        return DeliveryPersistenceResult(
            prompt_id=prompt.id,
            delivery_id=delivery.id,
            delivery_state=delivery.delivery_state,
            event_id=accepted.event.id,
            server_sequence=accepted.event.server_sequence,
            interview_state_version=accepted.event.interview_state_version,
            created=accepted.created,
            observation=observation,
        )

    async def _prompt_for_session(self, session_id: UUID, prompt_id: UUID) -> InterviewerPrompt:
        prompt = await self._session.scalar(
            select(InterviewerPrompt)
            # start_delivery validates Examiner-origin prompt provenance.  Load it
            # explicitly so AsyncSession never attempts implicit relationship I/O.
            .options(selectinload(InterviewerPrompt.examiner_decision))
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

    async def _lock_interview(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update(),
        )
        if interview is None:
            raise RealtimeControlSessionNotFound(f"InterviewSession not found: {session_id}")
        return interview

    async def _event_for_idempotency(
        self,
        session_id: UUID,
        idempotency_key: str,
    ) -> InterviewEvent | None:
        event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == session_id)
            .where(InterviewEvent.idempotency_key == idempotency_key),
        )
        return cast(InterviewEvent | None, event)

    async def _code_result_for_idempotent_event(
        self,
        *,
        event: InterviewEvent,
        expected_content_hash: str,
        expected_language: str,
        expected_trigger: str,
    ) -> CodeSnapshotPersistenceResult:
        if (
            event.source != "NATIVE_EDITOR"
            or event.event_type not in {"CODE_SNAPSHOT_CREATED", "MEANINGFUL_CODE_CHANGE"}
            or event.payload.get("content_hash") != expected_content_hash
            or event.payload.get("language") != expected_language
            or event.payload.get("trigger") != expected_trigger
        ):
            raise IdempotencyConflict(
                "Candidate code idempotency key conflicts with existing code observation"
            )
        observations = ObservationRepository(self._session)
        snapshot = await observations.code_snapshot_for_event(event.id)
        if snapshot is None:
            raise RealtimeControlConflict(
                "Idempotent code event exists without CodeSnapshot"
            )
        diff = await observations.code_diff_for_event(event.id)
        observation = await ObservationEngine(self._session).project_event(event.id)
        return CodeSnapshotPersistenceResult(
            snapshot_id=snapshot.id,
            version_number=snapshot.version_number,
            content_hash=snapshot.content_hash,
            interview_state_version=event.interview_state_version,
            created=False,
            event_id=event.id,
            diff_id=diff.id if diff else None,
            server_sequence=event.server_sequence,
            observation=observation,
        )

    async def _next_delivery_attempt(self, prompt_id: UUID) -> int:
        max_attempt = await self._session.scalar(
            select(func.max(InterviewerPromptDelivery.delivery_attempt)).where(
                InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
            ),
        )
        return int(max_attempt or 0) + 1


def normalize_source_code(source_code: str) -> str:
    return source_code.replace("\r\n", "\n").replace("\r", "\n")


def content_sha256(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def unified_diff(
    *,
    from_source: str,
    to_source: str,
    from_label: str,
    to_label: str,
) -> str:
    diff_lines = difflib.unified_diff(
        from_source.splitlines(),
        to_source.splitlines(),
        fromfile=from_label,
        tofile=to_label,
        lineterm="",
    )
    diff_text = "\n".join(diff_lines)
    return f"{diff_text}\n" if diff_text else ""

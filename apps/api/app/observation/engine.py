from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewerPromptDelivery
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.observation.repository import ObservationRepository

ObservationKind = Literal[
    "CANDIDATE_TRANSCRIPT_FINALIZED",
    "CODE_SNAPSHOT_CREATED",
    "CODE_MEANINGFULLY_CHANGED",
    "COUNTERQ_DELIVERY_COMPLETED",
    "COUNTERQ_DELIVERY_INTERRUPTED",
]

ObservationTriggerClass = Literal[
    "VOICE_TURN_COMPLETED",
    "CODE_EDIT_BURST",
    "INTERVIEWER_CONTEXT",
    "INTERRUPTION_CONTEXT",
]


class ObservationProjectionError(ValueError):
    pass


class ObservationSourceMissing(ObservationProjectionError):
    pass


@dataclass(frozen=True)
class StructuredObservation:
    kind: ObservationKind
    interview_session_id: UUID
    source_event_id: UUID
    source_event_watermark: int
    interview_state_version: int
    interview_stage: str
    occurred_at: datetime
    trigger_class: ObservationTriggerClass
    transcript_segment_id: UUID | None = None
    transcript_text: str | None = None
    code_snapshot_id: UUID | None = None
    code_snapshot_version: int | None = None
    code_content_hash: str | None = None
    code_source: str | None = None
    code_diff_id: UUID | None = None
    code_diff_content: str | None = None
    prompt_delivery_id: UUID | None = None
    associated_code_snapshot_id: UUID | None = None
    associated_code_snapshot_version: int | None = None


class ObservationEngine:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._observations = ObservationRepository(session)

    async def project_event(self, event_id: UUID) -> StructuredObservation:
        event = await self._session.get(InterviewEvent, event_id)
        if event is None:
            raise ObservationSourceMissing(f"InterviewEvent not found: {event_id}")

        if event.event_type == "TRANSCRIPT_FINALIZED":
            return await self._project_candidate_transcript(event)
        if event.event_type in {"CODE_SNAPSHOT_CREATED", "MEANINGFUL_CODE_CHANGE"}:
            return await self._project_code_event(event)
        if event.event_type == "COUNTERQ_UTTERANCE_DELIVERED":
            return await self._project_delivery_completed(event)
        if event.event_type == "CANDIDATE_INTERRUPTED_COUNTERQ":
            return await self._project_delivery_interrupted(event)
        raise ObservationProjectionError(f"Unsupported observation event type: {event.event_type}")

    async def _project_candidate_transcript(
        self,
        event: InterviewEvent,
    ) -> StructuredObservation:
        segment = await self._session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.interview_event_id == event.id),
        )
        if segment is None:
            raise ObservationSourceMissing(
                "TranscriptSegment missing for finalized transcript event"
            )
        code_snapshot = await self._observations.latest_code_snapshot_at_or_before_event(
            session_id=event.interview_session_id,
            server_sequence=event.server_sequence,
        )
        return StructuredObservation(
            kind="CANDIDATE_TRANSCRIPT_FINALIZED",
            interview_session_id=event.interview_session_id,
            source_event_id=event.id,
            source_event_watermark=event.server_sequence,
            interview_state_version=event.interview_state_version,
            interview_stage=segment.interview_stage,
            occurred_at=event.occurred_at,
            trigger_class="VOICE_TURN_COMPLETED",
            transcript_segment_id=segment.id,
            transcript_text=segment.text,
            associated_code_snapshot_id=code_snapshot.id if code_snapshot else None,
            associated_code_snapshot_version=(
                code_snapshot.version_number if code_snapshot else None
            ),
        )

    async def _project_code_event(self, event: InterviewEvent) -> StructuredObservation:
        snapshot = await self._observations.code_snapshot_for_event(event.id)
        if snapshot is None and event.code_snapshot_id is not None:
            snapshot = await self._session.get(CodeSnapshot, event.code_snapshot_id)
        if snapshot is None:
            raise ObservationSourceMissing("CodeSnapshot missing for code observation event")
        diff = await self._observations.code_diff_for_event(event.id)
        kind: ObservationKind = (
            "CODE_SNAPSHOT_CREATED"
            if event.event_type == "CODE_SNAPSHOT_CREATED"
            else "CODE_MEANINGFULLY_CHANGED"
        )
        trigger_class: ObservationTriggerClass = (
            "CODE_EDIT_BURST"
            if event.event_type == "MEANINGFUL_CODE_CHANGE"
            else "INTERVIEWER_CONTEXT"
        )
        return StructuredObservation(
            kind=kind,
            interview_session_id=event.interview_session_id,
            source_event_id=event.id,
            source_event_watermark=event.server_sequence,
            interview_state_version=event.interview_state_version,
            interview_stage=_string_payload(event, "interview_stage") or "UNKNOWN",
            occurred_at=event.occurred_at,
            trigger_class=trigger_class,
            code_snapshot_id=snapshot.id,
            code_snapshot_version=snapshot.version_number,
            code_content_hash=snapshot.content_hash,
            code_source=snapshot.source_code,
            code_diff_id=diff.id if diff else None,
            code_diff_content=diff.diff_content if diff else None,
        )

    async def _project_delivery_completed(self, event: InterviewEvent) -> StructuredObservation:
        segment = await self._session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.interview_event_id == event.id),
        )
        if segment is None:
            raise ObservationSourceMissing("TranscriptSegment missing for delivery event")
        delivery = await self._delivery_from_event(event)
        return StructuredObservation(
            kind="COUNTERQ_DELIVERY_COMPLETED",
            interview_session_id=event.interview_session_id,
            source_event_id=event.id,
            source_event_watermark=event.server_sequence,
            interview_state_version=event.interview_state_version,
            interview_stage=segment.interview_stage,
            occurred_at=event.occurred_at,
            trigger_class="INTERVIEWER_CONTEXT",
            transcript_segment_id=segment.id,
            transcript_text=segment.text,
            prompt_delivery_id=delivery.id if delivery else None,
        )

    async def _project_delivery_interrupted(self, event: InterviewEvent) -> StructuredObservation:
        delivery = await self._delivery_from_event(event)
        interview_stage = _string_payload(event, "interview_stage") or "UNKNOWN"
        return StructuredObservation(
            kind="COUNTERQ_DELIVERY_INTERRUPTED",
            interview_session_id=event.interview_session_id,
            source_event_id=event.id,
            source_event_watermark=event.server_sequence,
            interview_state_version=event.interview_state_version,
            interview_stage=interview_stage,
            occurred_at=event.occurred_at,
            trigger_class="INTERRUPTION_CONTEXT",
            prompt_delivery_id=delivery.id if delivery else None,
        )

    async def _delivery_from_event(
        self,
        event: InterviewEvent,
    ) -> InterviewerPromptDelivery | None:
        raw_delivery_id = _string_payload(event, "prompt_delivery_id")
        if raw_delivery_id is None:
            return None
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(InterviewerPromptDelivery.interview_session_id == event.interview_session_id)
            .where(InterviewerPromptDelivery.id == UUID(raw_delivery_id)),
        )
        return cast(InterviewerPromptDelivery | None, delivery)


def _string_payload(event: InterviewEvent, key: str) -> str | None:
    value = event.payload.get(key)
    return cast(str, value) if isinstance(value, str) else None

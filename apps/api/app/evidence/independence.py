from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewerPrompt,
    InterviewerPromptDelivery,
)
from app.observation.models import InterviewEvent, TranscriptSegment


@dataclass(frozen=True)
class IndependenceAttribution:
    level: str | None
    reason: str

    @property
    def resolved(self) -> bool:
        return self.level is not None


class IndependenceAttributionService:
    """Attribute Simulation independence from canonical delivery/causality only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_response(self, response: CandidateResponse) -> IndependenceAttribution:
        if response.interviewer_prompt_id is None:
            if await self._follows_unresolved_interrupted_probe(response):
                return IndependenceAttribution(
                    None, "INTERRUPTED_PROBE_WITHOUT_EXACT_DELIVERY_TRANSCRIPT"
                )
            return IndependenceAttribution("INDEPENDENT", "SPONTANEOUS_RESPONSE")
        delivered = await self._actual_delivery(response.interviewer_prompt_id)
        if delivered is None:
            return IndependenceAttribution(None, "PROMPT_DELIVERY_UNPROVED")
        prompt, _delivery, _event = delivered
        if prompt.kind == "PROBE":
            return IndependenceAttribution("AFTER_PROBE", "ACTUAL_DIAGNOSTIC_PROBE_DELIVERY")
        return IndependenceAttribution("INDEPENDENT", "NON_PROBE_INTERVIEWER_CONTEXT")

    async def for_direct_event(self, event: InterviewEvent) -> IndependenceAttribution:
        delivered = await self._latest_actual_delivery_before(event)
        if delivered is None:
            return IndependenceAttribution("INDEPENDENT", "NO_PRIOR_DELIVERED_PROBE")
        prompt, _delivery, delivery_event = delivered
        if prompt.kind != "PROBE":
            return IndependenceAttribution("INDEPENDENT", "PRIOR_DELIVERY_WAS_NOT_PROBE")
        if event.causation_id == delivery_event.id or event.correlation_id == delivery_event.id:
            return IndependenceAttribution("AFTER_PROBE", "EXPLICIT_EVENT_CAUSAL_LINK")
        return IndependenceAttribution(None, "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS")

    async def _follows_unresolved_interrupted_probe(self, response: CandidateResponse) -> bool:
        response_event = await self._session.scalar(
            select(InterviewEvent)
            .join(
                CandidateResponseSource,
                CandidateResponseSource.interview_event_id == InterviewEvent.id,
            )
            .where(CandidateResponseSource.candidate_response_id == response.id)
            .order_by(InterviewEvent.server_sequence)
            .limit(1)
        )
        if response_event is None:
            return False
        previous_candidate_sequence = await self._session.scalar(
            select(InterviewEvent.server_sequence)
            .where(
                InterviewEvent.interview_session_id == response.interview_session_id,
                InterviewEvent.event_type == "TRANSCRIPT_FINALIZED",
                InterviewEvent.source == "CANDIDATE_VOICE",
                InterviewEvent.server_sequence < response_event.server_sequence,
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        interrupted_event = await self._session.scalar(
            select(InterviewEvent)
            .where(
                InterviewEvent.interview_session_id == response.interview_session_id,
                InterviewEvent.event_type == "CANDIDATE_INTERRUPTED_COUNTERQ",
                InterviewEvent.server_sequence > int(previous_candidate_sequence or 0),
                InterviewEvent.server_sequence < response_event.server_sequence,
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        if interrupted_event is None:
            return False
        prompt_id = interrupted_event.payload.get("interviewer_prompt_id")
        delivery_id = interrupted_event.payload.get("prompt_delivery_id")
        if not isinstance(prompt_id, str) or not isinstance(delivery_id, str):
            return False
        try:
            prompt_uuid = UUID(prompt_id)
            delivery_uuid = UUID(delivery_id)
        except ValueError:
            return False
        prompt = await self._session.get(InterviewerPrompt, prompt_uuid)
        delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
        return bool(
            prompt is not None
            and prompt.kind == "PROBE"
            and delivery is not None
            and delivery.actual_transcript_segment_id is None
        )

    async def _latest_actual_delivery_before(
        self, event: InterviewEvent
    ) -> tuple[InterviewerPrompt, InterviewerPromptDelivery, InterviewEvent] | None:
        delivery_event = await self._session.scalar(
            select(InterviewEvent)
            .where(
                InterviewEvent.interview_session_id == event.interview_session_id,
                InterviewEvent.event_type == "COUNTERQ_UTTERANCE_DELIVERED",
                InterviewEvent.source == "COUNTERQ_VOICE",
                InterviewEvent.server_sequence < event.server_sequence,
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        if delivery_event is None:
            return None
        return await self._actual_delivery_from_event(delivery_event)

    async def _actual_delivery(
        self, prompt_id: UUID
    ) -> tuple[InterviewerPrompt, InterviewerPromptDelivery, InterviewEvent] | None:
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(
                InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
                InterviewerPromptDelivery.delivery_state == "DELIVERED",
                InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
            )
            .order_by(InterviewerPromptDelivery.delivery_attempt.desc())
            .limit(1)
        )
        if delivery is None:
            return None
        segment = await self._session.get(TranscriptSegment, delivery.actual_transcript_segment_id)
        if segment is None:
            return None
        event = await self._session.get(InterviewEvent, segment.interview_event_id)
        prompt = await self._session.get(InterviewerPrompt, prompt_id)
        if (
            event is None
            or prompt is None
            or event.event_type != "COUNTERQ_UTTERANCE_DELIVERED"
            or event.source != "COUNTERQ_VOICE"
        ):
            return None
        return prompt, delivery, event

    async def _actual_delivery_from_event(
        self, event: InterviewEvent
    ) -> tuple[InterviewerPrompt, InterviewerPromptDelivery, InterviewEvent] | None:
        delivery_id = event.payload.get("prompt_delivery_id")
        if not isinstance(delivery_id, str):
            return None
        try:
            delivery_uuid = UUID(delivery_id)
        except ValueError:
            return None
        delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
        if (
            delivery is None
            or delivery.delivery_state != "DELIVERED"
            or delivery.actual_transcript_segment_id is None
        ):
            return None
        prompt = await self._session.get(InterviewerPrompt, delivery.interviewer_prompt_id)
        segment = await self._session.get(TranscriptSegment, delivery.actual_transcript_segment_id)
        if prompt is None or segment is None or segment.interview_event_id != event.id:
            return None
        return prompt, delivery, event

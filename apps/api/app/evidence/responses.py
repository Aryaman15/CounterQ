from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewerPrompt,
    InterviewerPromptDelivery,
)
from app.observation.models import InterviewEvent, TranscriptSegment


class CandidateResponseMaterializer:
    """Materialize one canonical response for one finalized candidate turn.

    The caller must already own the InterviewSession row lock used by event
    acceptance. Prompt association is based only on server sequence and proved
    delivery; timestamps and authorized intent are deliberately ignored.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def materialize(
        self,
        *,
        interview_session_id: UUID,
        event: InterviewEvent,
        segment: TranscriptSegment,
    ) -> CandidateResponse:
        existing = await self._session.scalar(
            select(CandidateResponse)
            .join(
                CandidateResponseSource,
                CandidateResponseSource.candidate_response_id == CandidateResponse.id,
            )
            .where(
                CandidateResponseSource.interview_session_id == interview_session_id,
                CandidateResponseSource.interview_event_id == event.id,
            )
        )
        if existing is not None:
            return cast(CandidateResponse, existing)

        prompt = await self._delivered_prompt_for_turn(
            interview_session_id=interview_session_id,
            candidate_server_sequence=event.server_sequence,
        )
        repository = InterviewInteractionRepository(self._session)
        response = await repository.add_response(
            interview_session_id=interview_session_id,
            interviewer_prompt_id=prompt.id if prompt is not None else None,
            started_at=segment.started_at,
            ended_at=segment.ended_at or event.occurred_at,
            completion_reason="COMPLETE" if prompt is not None else "SPONTANEOUS",
        )
        await repository.add_response_source(
            interview_session_id=interview_session_id,
            candidate_response_id=response.id,
            interview_event_id=event.id,
            source_role="PRIMARY",
            sequence=1,
        )
        if prompt is not None and prompt.status == "DELIVERED":
            prompt.status = "ANSWERED"
        await self._session.flush()
        return response

    async def _delivered_prompt_for_turn(
        self,
        *,
        interview_session_id: UUID,
        candidate_server_sequence: int,
    ) -> InterviewerPrompt | None:
        previous_candidate_sequence = await self._session.scalar(
            select(InterviewEvent.server_sequence)
            .where(
                InterviewEvent.interview_session_id == interview_session_id,
                InterviewEvent.event_type == "TRANSCRIPT_FINALIZED",
                InterviewEvent.source == "CANDIDATE_VOICE",
                InterviewEvent.server_sequence < candidate_server_sequence,
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        lower_bound = int(previous_candidate_sequence or 0)

        # An interrupted attempt after the last completed delivery makes the heard
        # prompt ambiguous when no exact partial transcript was persisted. In that
        # case no prompt provenance is attached and intended_text remains undisclosed.
        latest_prompt_event = await self._session.scalar(
            select(InterviewEvent)
            .where(
                InterviewEvent.interview_session_id == interview_session_id,
                InterviewEvent.server_sequence > lower_bound,
                InterviewEvent.server_sequence < candidate_server_sequence,
                InterviewEvent.event_type.in_(
                    ("COUNTERQ_UTTERANCE_DELIVERED", "CANDIDATE_INTERRUPTED_COUNTERQ")
                ),
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        if latest_prompt_event is None or latest_prompt_event.event_type != (
            "COUNTERQ_UTTERANCE_DELIVERED"
        ):
            return None
        delivery_id = latest_prompt_event.payload.get("prompt_delivery_id")
        if not isinstance(delivery_id, str):
            return None
        delivery = await self._session.get(InterviewerPromptDelivery, UUID(delivery_id))
        if (
            delivery is None
            or delivery.interview_session_id != interview_session_id
            or delivery.delivery_state != "DELIVERED"
            or delivery.actual_transcript_segment_id is None
        ):
            return None
        return cast(
            InterviewerPrompt | None,
            await self._session.get(InterviewerPrompt, delivery.interviewer_prompt_id),
        )

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.independence import is_response_bearing_prompt
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
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
        if prompt is None:
            prompt = await self._continued_coach_assistance_prompt(
                interview_session_id=interview_session_id,
                candidate_event=event,
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
        try:
            delivery_uuid = UUID(delivery_id)
        except ValueError:
            return None
        delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
        if (
            delivery is None
            or delivery.interview_session_id != interview_session_id
            or delivery.delivery_state != "DELIVERED"
            or delivery.actual_transcript_segment_id is None
        ):
            return None
        prompt = await self._session.get(InterviewerPrompt, delivery.interviewer_prompt_id)
        if prompt is None or not is_response_bearing_prompt(prompt):
            return None
        return cast(InterviewerPrompt, prompt)

    async def _continued_coach_assistance_prompt(
        self,
        *,
        interview_session_id: UUID,
        candidate_event: InterviewEvent,
    ) -> InterviewerPrompt | None:
        """Continue only the immediately preceding proved Coach assistance chain."""

        mode = await self._session.scalar(
            select(InterviewConfiguration.mode)
            .join(
                InterviewSession,
                InterviewSession.interview_configuration_id == InterviewConfiguration.id,
            )
            .where(InterviewSession.id == interview_session_id)
        )
        if mode != "COACH":
            return None

        previous_event = await self._session.scalar(
            select(InterviewEvent)
            .where(
                InterviewEvent.interview_session_id == interview_session_id,
                InterviewEvent.event_type == "TRANSCRIPT_FINALIZED",
                InterviewEvent.source == "CANDIDATE_VOICE",
                InterviewEvent.server_sequence < candidate_event.server_sequence,
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        if (
            previous_event is None
            or previous_event.interview_state_version
            != candidate_event.interview_state_version
        ):
            return None

        previous_response = await self._session.scalar(
            select(CandidateResponse)
            .join(
                CandidateResponseSource,
                CandidateResponseSource.candidate_response_id == CandidateResponse.id,
            )
            .where(
                CandidateResponse.interview_session_id == interview_session_id,
                CandidateResponseSource.interview_event_id == previous_event.id,
            )
            .limit(1)
        )
        if (
            previous_response is None
            or previous_response.interviewer_prompt_id is None
            or previous_response.completion_reason == "SPONTANEOUS"
        ):
            return None

        prompt = await self._session.get(
            InterviewerPrompt, previous_response.interviewer_prompt_id
        )
        if (
            prompt is None
            or prompt.interview_session_id != interview_session_id
            or prompt.kind != "INSTRUCTION"
            or prompt.assistance_type is None
            or prompt.hint_level is None
        ):
            return None
        if not await self._has_proved_assistance_delivery_before(
            interview_session_id=interview_session_id,
            prompt_id=prompt.id,
            server_sequence=previous_event.server_sequence,
        ):
            return None
        if await self._has_newer_response_bearing_delivery(
            interview_session_id=interview_session_id,
            after_sequence=previous_event.server_sequence,
            before_sequence=candidate_event.server_sequence,
        ):
            return None
        return cast(InterviewerPrompt, prompt)

    async def _has_proved_assistance_delivery_before(
        self,
        *,
        interview_session_id: UUID,
        prompt_id: UUID,
        server_sequence: int,
    ) -> bool:
        deliveries = list(
            await self._session.scalars(
                select(InterviewerPromptDelivery)
                .where(
                    InterviewerPromptDelivery.interview_session_id
                    == interview_session_id,
                    InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
                    InterviewerPromptDelivery.delivery_state.in_(
                        ("DELIVERED", "PARTIALLY_DELIVERED")
                    ),
                    InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
                )
                .order_by(InterviewerPromptDelivery.delivery_attempt.desc())
            )
        )
        for delivery in deliveries:
            segment = await self._session.get(
                TranscriptSegment, delivery.actual_transcript_segment_id
            )
            if segment is None or segment.interview_session_id != interview_session_id:
                continue
            event = await self._session.get(InterviewEvent, segment.interview_event_id)
            if (
                event is not None
                and event.interview_session_id == interview_session_id
                and event.server_sequence < server_sequence
                and self._delivery_matches_event(delivery, event)
            ):
                return True
        return False

    async def _has_newer_response_bearing_delivery(
        self,
        *,
        interview_session_id: UUID,
        after_sequence: int,
        before_sequence: int,
    ) -> bool:
        lifecycle_events = list(
            await self._session.scalars(
                select(InterviewEvent)
                .where(
                    InterviewEvent.interview_session_id == interview_session_id,
                    InterviewEvent.server_sequence > after_sequence,
                    InterviewEvent.server_sequence < before_sequence,
                    InterviewEvent.event_type.in_(
                        ("COUNTERQ_UTTERANCE_DELIVERED", "CANDIDATE_INTERRUPTED_COUNTERQ")
                    ),
                )
                .order_by(InterviewEvent.server_sequence)
            )
        )
        for event in lifecycle_events:
            delivery_id = event.payload.get("prompt_delivery_id")
            if not isinstance(delivery_id, str):
                continue
            try:
                delivery_uuid = UUID(delivery_id)
            except ValueError:
                continue
            delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
            if (
                delivery is None
                or delivery.interview_session_id != interview_session_id
                or delivery.actual_transcript_segment_id is None
                or not self._delivery_matches_event(delivery, event)
            ):
                continue
            segment = await self._session.get(
                TranscriptSegment, delivery.actual_transcript_segment_id
            )
            if segment is None or segment.interview_event_id != event.id:
                continue
            prompt = await self._session.get(
                InterviewerPrompt, delivery.interviewer_prompt_id
            )
            if prompt is not None and is_response_bearing_prompt(prompt):
                return True
        return False

    @staticmethod
    def _delivery_matches_event(
        delivery: InterviewerPromptDelivery, event: InterviewEvent
    ) -> bool:
        return (
            delivery.delivery_state == "DELIVERED"
            and event.event_type == "COUNTERQ_UTTERANCE_DELIVERED"
        ) or (
            delivery.delivery_state == "PARTIALLY_DELIVERED"
            and event.event_type == "CANDIDATE_INTERRUPTED_COUNTERQ"
        )

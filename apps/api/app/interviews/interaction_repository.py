from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewerPrompt,
    InterviewerPromptDelivery,
)


class InterviewInteractionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_prompt(
        self,
        *,
        interview_session_id: UUID,
        origin: str,
        kind: str,
        intent: str,
        status: str,
        examiner_decision_id: UUID | None = None,
        probe_strategy: str | None = None,
        target_claim_id: UUID | None = None,
        authorized_at: datetime | None = None,
    ) -> InterviewerPrompt:
        prompt = InterviewerPrompt(
            interview_session_id=interview_session_id,
            examiner_decision_id=examiner_decision_id,
            origin=origin,
            kind=kind,
            probe_strategy=probe_strategy,
            target_claim_id=target_claim_id,
            intent=intent,
            status=status,
            authorized_at=authorized_at,
        )
        self._session.add(prompt)
        await self._session.flush()
        return prompt

    async def add_delivery(
        self,
        *,
        interview_session_id: UUID,
        interviewer_prompt_id: UUID,
        delivery_attempt: int,
        intended_text: str,
        delivery_state: str,
        started_at: datetime,
        actual_transcript_segment_id: UUID | None = None,
        completed_at: datetime | None = None,
        interrupted_at: datetime | None = None,
        realtime_provider_event_id: str | None = None,
        ai_invocation_id: UUID | None = None,
    ) -> InterviewerPromptDelivery:
        delivery = InterviewerPromptDelivery(
            interview_session_id=interview_session_id,
            interviewer_prompt_id=interviewer_prompt_id,
            delivery_attempt=delivery_attempt,
            intended_text=intended_text,
            actual_transcript_segment_id=actual_transcript_segment_id,
            delivery_state=delivery_state,
            started_at=started_at,
            completed_at=completed_at,
            interrupted_at=interrupted_at,
            realtime_provider_event_id=realtime_provider_event_id,
            ai_invocation_id=ai_invocation_id,
        )
        self._session.add(delivery)
        await self._session.flush()
        return delivery

    async def add_response(
        self,
        *,
        interview_session_id: UUID,
        started_at: datetime,
        completion_reason: str,
        interviewer_prompt_id: UUID | None = None,
        ended_at: datetime | None = None,
        summary: str | None = None,
    ) -> CandidateResponse:
        response = CandidateResponse(
            interview_session_id=interview_session_id,
            interviewer_prompt_id=interviewer_prompt_id,
            started_at=started_at,
            ended_at=ended_at,
            completion_reason=completion_reason,
            summary=summary,
        )
        self._session.add(response)
        await self._session.flush()
        return response

    async def add_response_source(
        self,
        *,
        interview_session_id: UUID,
        candidate_response_id: UUID,
        interview_event_id: UUID,
        source_role: str,
        sequence: int,
    ) -> CandidateResponseSource:
        source = CandidateResponseSource(
            interview_session_id=interview_session_id,
            candidate_response_id=candidate_response_id,
            interview_event_id=interview_event_id,
            source_role=source_role,
            sequence=sequence,
        )
        self._session.add(source)
        await self._session.flush()
        return source

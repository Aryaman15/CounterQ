"""Durable facts shared by Coach assistance authorization and delivery gates."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import CandidateResponse, CandidateResponseSource
from app.observation.models import InterviewEvent, TranscriptSegment


async def initial_final_defense_answer_captured(
    session: AsyncSession,
    interview_session_id: UUID,
    *,
    before_sequence: int | None = None,
) -> bool:
    """Return a software-derived fact, never a client-supplied flag."""

    statement = (
        select(CandidateResponse.id)
        .join(
            CandidateResponseSource,
            CandidateResponseSource.candidate_response_id == CandidateResponse.id,
        )
        .join(InterviewEvent, InterviewEvent.id == CandidateResponseSource.interview_event_id)
        .join(TranscriptSegment, TranscriptSegment.interview_event_id == InterviewEvent.id)
        .where(
            CandidateResponse.interview_session_id == interview_session_id,
            CandidateResponse.ended_at.is_not(None),
            TranscriptSegment.speaker == "CANDIDATE",
            TranscriptSegment.interview_stage == "FINAL_DEFENSE",
        )
    )
    if before_sequence is not None:
        statement = statement.where(InterviewEvent.server_sequence < before_sequence)
    return await session.scalar(statement.limit(1)) is not None

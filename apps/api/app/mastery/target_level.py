"""Server-owned Phase 1 authority for the current Mastery target level."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewConfiguration, InterviewSession
from app.mastery.policy import InterviewLevel


class MasteryTargetLevelResolver:
    """Resolve the latest completed interview level with deterministic ordering."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, user_id: UUID) -> InterviewLevel:
        value = await self._session.scalar(
            select(InterviewConfiguration.level)
            .join(
                InterviewSession,
                InterviewSession.interview_configuration_id == InterviewConfiguration.id,
            )
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status == "COMPLETED",
                InterviewSession.completed_at.is_not(None),
            )
            .order_by(
                InterviewSession.completed_at.desc(),
                InterviewSession.id.desc(),
            )
            .limit(1)
        )
        return cast(InterviewLevel, value) if value is not None else "NEW_GRAD"

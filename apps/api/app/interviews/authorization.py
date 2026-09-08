from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewSession


class OwnedInterviewNotFound(LookupError):
    """No candidate-visible interview exists for this principal and identifier."""


class InterviewOwnershipRepository:
    """The reusable server-side boundary for candidate interview ownership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(
        self,
        *,
        principal_user_id: UUID,
        interview_session_id: UUID,
        allowed_statuses: Collection[str] | None = None,
        for_update: bool = False,
    ) -> InterviewSession:
        statement: Select[tuple[InterviewSession]] = select(InterviewSession).where(
            InterviewSession.id == interview_session_id,
            InterviewSession.user_id == principal_user_id,
        )
        if allowed_statuses is not None:
            statement = statement.where(InterviewSession.status.in_(tuple(allowed_statuses)))
        if for_update:
            statement = statement.with_for_update()
        interview = await self._session.scalar(statement)
        if interview is None:
            raise OwnedInterviewNotFound("Interview session was not found")
        return interview

"""Current-candidate interview history derived from canonical session facts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.contracts import (
    CandidateInterviewHistoryItem,
    CandidateInterviewHistoryResponse,
)
from app.interviews.models import InterviewConfiguration, InterviewSession
from app.interviews.template_policy import template_for_duration
from app.problems.contracts import CandidateLanguage
from app.problems.models import ProblemVersion

HistoryState = Literal["all", "in_progress", "completed"]
_IN_PROGRESS_STATUSES = ("READY", "ACTIVE", "RECONNECTING")


class CandidateInterviewHistoryReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(
        self,
        *,
        user_id: UUID,
        state: HistoryState,
        limit: int,
        offset: int,
        now: datetime | None = None,
    ) -> CandidateInterviewHistoryResponse:
        current_time = now or datetime.now(UTC)
        statement = (
            select(InterviewSession, InterviewConfiguration, ProblemVersion)
            .join(
                InterviewConfiguration,
                InterviewConfiguration.id == InterviewSession.interview_configuration_id,
            )
            .join(ProblemVersion, ProblemVersion.id == InterviewSession.problem_version_id)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.status != "DELETION_PENDING",
            )
        )
        if state == "in_progress":
            statement = statement.where(InterviewSession.status.in_(_IN_PROGRESS_STATUSES))
        elif state == "completed":
            statement = statement.where(InterviewSession.status == "COMPLETED")
        rows = (
            await self._session.execute(
                statement.order_by(
                    InterviewSession.started_at.desc(),
                    InterviewSession.id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return CandidateInterviewHistoryResponse(
            items=[self._item(*row, now=current_time) for row in rows[:limit]],
            limit=limit,
            offset=offset,
            has_more=len(rows) > limit,
        )

    @staticmethod
    def _item(
        interview: InterviewSession,
        configuration: InterviewConfiguration,
        problem: ProblemVersion,
        *,
        now: datetime,
    ) -> CandidateInterviewHistoryItem:
        if interview.status in _IN_PROGRESS_STATUSES:
            display_status: Literal["IN_PROGRESS", "COMPLETED", "ENDED"] = "IN_PROGRESS"
        elif interview.status == "COMPLETED":
            display_status = "COMPLETED"
        else:
            display_status = "ENDED"
        template = template_for_duration(configuration.configured_duration_seconds)
        return CandidateInterviewHistoryItem(
            interview_session_id=interview.id,
            problem_title=problem.title,
            template=template.template if template is not None else "CUSTOM",
            mode=cast(Literal["COACH", "SIMULATION"], configuration.mode),
            language=cast(CandidateLanguage, configuration.language),
            candidate_level=configuration.level,
            status=interview.status,
            display_status=display_status,
            started_at=interview.started_at,
            completed_at=interview.completed_at,
            deadline_at=interview.deadline_at,
            configured_duration_seconds=configuration.configured_duration_seconds,
            can_resume=(
                interview.status in _IN_PROGRESS_STATUSES
                and interview.deadline_at > now
            ),
            interview_path=f"/interview/{interview.id}",
            report_path=f"/interview/{interview.id}/report",
            countermap_path=f"/interview/{interview.id}/countermap",
        )

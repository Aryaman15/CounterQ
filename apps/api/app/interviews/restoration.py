"""Canonical, candidate-safe projection for refreshing an active interview."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.interviews.completion import InterviewCompletionService, TerminalReason
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery, InterviewSession
from app.interviews.runtime import InterviewRuntime
from app.interviews.template_policy import template_for_duration
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.contracts import CandidateProblemDetail, candidate_problem_detail
from app.problems.models import ProblemVersion

RESTORE_PROTOCOL_VERSION: Literal["session.restore.v1"] = "session.restore.v1"
MAX_RECENT_CONVERSATION_TURNS = 40
VISIBLE_DELIVERY_STATES = ("DELIVERED", "INTERRUPTED")


class DevelopmentInterviewNotResumable(ValueError):
    """The supplied local development session pointer cannot be restored."""


@dataclass(frozen=True)
class RestoredCodeSnapshot:
    id: UUID
    version_number: int
    language: str
    source_code: str
    content_hash: str


@dataclass(frozen=True)
class RestoredConversationTurn:
    id: UUID
    speaker: Literal["CANDIDATE", "COUNTERQ"]
    text: str
    sequence: int
    occurred_at: datetime
    delivery_state: str | None


@dataclass(frozen=True)
class RestoredUnresolvedPrompt:
    id: UUID
    kind: str
    status: str


@dataclass(frozen=True)
class RestoredInterview:
    interview: InterviewSession
    template: str
    time_remaining_seconds: int
    time_pressure: str
    code_snapshot: RestoredCodeSnapshot | None
    conversation: tuple[RestoredConversationTurn, ...]
    unresolved_prompt: RestoredUnresolvedPrompt | None
    highest_client_sequence: int
    terminal_reason: TerminalReason | None
    problem: CandidateProblemDetail


class SessionRestorationService:
    """Reads durable session truth without treating browser state as canonical."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def restore(
        self,
        *,
        interview_session_id: UUID,
        client_instance_id: str | None,
        reconcile_orphaned_deliveries: bool = False,
    ) -> RestoredInterview:
        interview = await self._interview(interview_session_id)
        if interview.status == "ACTIVE" and self._clock() >= interview.deadline_at:
            await InterviewCompletionService(
                self._session, clock=self._clock
            ).reconcile_expired(interview.id)
            interview = await self._interview(interview_session_id)
        if interview.status not in {"ACTIVE", "COMPLETED"}:
            raise DevelopmentInterviewNotResumable("Interview session is not resumable")

        if reconcile_orphaned_deliveries and interview.status == "ACTIVE":
            await self._reconcile_orphaned_deliveries(interview.id)

        timing = await InterviewRuntime(self._session).time_policy(interview.id)
        snapshot = await self._latest_code_snapshot(interview.id)
        conversation = await self._recent_candidate_visible_conversation(interview.id)
        unresolved_prompt = await self._unresolved_prompt(interview.id)
        highest_client_sequence = await self._highest_client_sequence(
            interview.id,
            client_instance_id,
        )
        template = template_for_duration(interview.configuration.configured_duration_seconds)
        if (
            interview.interview_pack_version.problem_version_id
            != interview.problem_version_id
        ):
            raise DevelopmentInterviewNotResumable(
                "Interview Pack does not match the session ProblemVersion"
            )
        try:
            problem = candidate_problem_detail(
                interview.problem_version,
                cast(
                    Literal["cpp", "python", "java"],
                    interview.configuration.language,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DevelopmentInterviewNotResumable(
                "Interview problem projection is unavailable"
            ) from exc
        return RestoredInterview(
            interview=interview,
            template=template.template if template is not None else "CUSTOM",
            time_remaining_seconds=timing.time_remaining_seconds if timing else 0,
            time_pressure=timing.pressure if timing else "NORMAL",
            code_snapshot=snapshot,
            conversation=conversation,
            unresolved_prompt=unresolved_prompt,
            highest_client_sequence=highest_client_sequence,
            terminal_reason=(
                await self._terminal_reason(interview.id)
                if interview.status == "COMPLETED"
                else None
            ),
            problem=problem,
        )

    async def _terminal_reason(self, interview_session_id: UUID) -> TerminalReason:
        from app.interviews.models import InterviewStageTransition

        transition = await self._session.scalar(
            select(InterviewStageTransition)
            .where(InterviewStageTransition.interview_session_id == interview_session_id)
            .where(InterviewStageTransition.to_stage == "WRAP_UP")
            .order_by(InterviewStageTransition.state_version.desc())
            .limit(1),
        )
        return (
            "TIME_EXPIRED"
            if transition and transition.trigger == "HARD_TIME_CONTROL"
            else "USER_ENDED"
        )

    async def _interview(self, interview_session_id: UUID) -> InterviewSession:
        interview = await self._session.scalar(
            select(InterviewSession)
            .options(
                joinedload(InterviewSession.configuration),
                joinedload(InterviewSession.problem_version).joinedload(ProblemVersion.problem),
                joinedload(InterviewSession.interview_pack_version),
                selectinload(InterviewSession.budget),
            )
            .where(InterviewSession.id == interview_session_id),
        )
        if interview is None:
            raise DevelopmentInterviewNotResumable("Interview session was not found")
        return interview

    async def _reconcile_orphaned_deliveries(self, interview_session_id: UUID) -> None:
        deliveries = list(
            (
                await self._session.scalars(
                    select(InterviewerPromptDelivery)
                    .options(selectinload(InterviewerPromptDelivery.interviewer_prompt))
                    .where(InterviewerPromptDelivery.interview_session_id == interview_session_id)
                    .where(InterviewerPromptDelivery.delivery_state == "STARTED")
                    .with_for_update(),
                )
            ).all()
        )
        for delivery in deliveries:
            # Browser disappearance cannot prove completion. Preserve no actual text.
            delivery.delivery_state = "INTERRUPTED"
            delivery.interrupted_at = datetime.now(UTC)
            if delivery.interviewer_prompt.status != "DELIVERED":
                delivery.interviewer_prompt.status = "INTERRUPTED"
        if deliveries:
            await self._session.flush()

    async def _latest_code_snapshot(
        self,
        interview_session_id: UUID,
    ) -> RestoredCodeSnapshot | None:
        snapshot = await self._session.scalar(
            select(CodeSnapshot)
            .where(CodeSnapshot.interview_session_id == interview_session_id)
            .order_by(CodeSnapshot.version_number.desc())
            .limit(1),
        )
        if snapshot is None:
            return None
        return RestoredCodeSnapshot(
            id=snapshot.id,
            version_number=snapshot.version_number,
            language=snapshot.language,
            source_code=snapshot.source_code,
            content_hash=snapshot.content_hash,
        )

    async def _recent_candidate_visible_conversation(
        self,
        interview_session_id: UUID,
    ) -> tuple[RestoredConversationTurn, ...]:
        rows = list(
            (
                await self._session.scalars(
                    select(TranscriptSegment)
                    .outerjoin(
                        InterviewerPromptDelivery,
                        InterviewerPromptDelivery.actual_transcript_segment_id
                        == TranscriptSegment.id,
                    )
                    .where(TranscriptSegment.interview_session_id == interview_session_id)
                    .where(
                        or_(
                            TranscriptSegment.speaker == "CANDIDATE",
                            InterviewerPromptDelivery.delivery_state.in_(
                                VISIBLE_DELIVERY_STATES
                            ),
                        )
                    )
                    .order_by(TranscriptSegment.sequence.desc())
                    .limit(MAX_RECENT_CONVERSATION_TURNS),
                )
            ).all()
        )
        return tuple(
            RestoredConversationTurn(
                id=segment.id,
                speaker=cast(Literal["CANDIDATE", "COUNTERQ"], segment.speaker),
                text=segment.text,
                sequence=segment.sequence,
                occurred_at=segment.ended_at or segment.started_at,
                delivery_state=segment.delivery_state,
            )
            for segment in reversed(rows)
        )

    async def _unresolved_prompt(
        self,
        interview_session_id: UUID,
    ) -> RestoredUnresolvedPrompt | None:
        prompt = await self._session.scalar(
            select(InterviewerPrompt)
            .where(InterviewerPrompt.interview_session_id == interview_session_id)
            .where(InterviewerPrompt.status == "AUTHORIZED")
            .order_by(
                InterviewerPrompt.authorized_at.desc().nullslast(),
                InterviewerPrompt.created_at.desc(),
            )
            .limit(1),
        )
        if prompt is None:
            return None
        return RestoredUnresolvedPrompt(id=prompt.id, kind=prompt.kind, status=prompt.status)

    async def _highest_client_sequence(
        self,
        interview_session_id: UUID,
        client_instance_id: str | None,
    ) -> int:
        if not client_instance_id:
            return 0
        highest = await self._session.scalar(
            select(func.max(InterviewEvent.client_sequence))
            .where(InterviewEvent.interview_session_id == interview_session_id)
            .where(InterviewEvent.client_instance_id == client_instance_id),
        )
        return int(highest or 0)

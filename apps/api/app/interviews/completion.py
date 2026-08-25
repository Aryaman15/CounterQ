"""Authoritative terminal closure for one interview session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    InterviewStageTransition,
)
from app.interviews.runtime import InterviewRuntime, SessionNotFound, TransitionCommand
from app.interviews.state_machine import TransitionContext

TerminalReason = Literal["USER_ENDED", "TIME_EXPIRED"]


class DeadlineNotReached(ValueError):
    """A client cannot complete an interview for time before server time expires."""


@dataclass(frozen=True)
class CompletionResult:
    interview: InterviewSession
    terminal_reason: TerminalReason
    created: bool
    transitions: tuple[InterviewStageTransition, ...]


class InterviewCompletionService:
    """Composes frozen WRAP_UP and COMPLETED transitions in one short transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def complete(
        self,
        *,
        session_id: UUID,
        reason: TerminalReason,
        expected_state_version: int | None,
        idempotency_key: str,
    ) -> CompletionResult:
        interview = await self._lock_session(session_id)
        if interview.status == "COMPLETED" or interview.current_stage == "COMPLETED":
            return CompletionResult(
                interview=interview,
                terminal_reason=await self._terminal_reason(interview.id),
                created=False,
                transitions=(),
            )
        if reason == "USER_ENDED" and self._clock() >= interview.deadline_at:
            # The deadline is server truth when both terminal requests race.
            reason = "TIME_EXPIRED"
        if reason == "TIME_EXPIRED" and self._clock() < interview.deadline_at:
            raise DeadlineNotReached("The authoritative interview deadline has not been reached")

        # The caller owns the transaction. Nothing below commits independently.
        await self._terminalize_prompt_lifecycle(interview.id)
        runtime = InterviewRuntime(self._session, clock=self._clock)
        transitions: list[InterviewStageTransition] = []
        context = TransitionContext(
            trigger="CANDIDATE_REQUESTED_FINISH" if reason == "USER_ENDED" else "HARD_TIME_CONTROL",
            candidate_requested_finish=reason == "USER_ENDED",
            hard_time_control=reason == "TIME_EXPIRED",
        )
        if interview.current_stage != "WRAP_UP":
            transitions.append(
                await runtime.transition(
                    TransitionCommand(
                        session_id=interview.id,
                        to_stage="WRAP_UP",
                        trigger=context.trigger,
                        expected_state_version=(
                            expected_state_version
                            if expected_state_version is not None
                            else interview.state_version
                        ),
                        occurred_at=self._clock(),
                        idempotency_key=f"{idempotency_key}:wrap-up",
                        context=context,
                    )
                )
            )
        transitions.append(
            await runtime.transition(
                TransitionCommand(
                    session_id=interview.id,
                    to_stage="COMPLETED",
                    trigger="CLOSING_COMPLETE",
                    expected_state_version=interview.state_version,
                    occurred_at=self._clock(),
                    idempotency_key=f"{idempotency_key}:completed",
                    context=context,
                )
            )
        )
        return CompletionResult(
            interview=interview,
            terminal_reason=reason,
            created=True,
            transitions=tuple(transitions),
        )

    async def reconcile_expired(self, session_id: UUID) -> CompletionResult | None:
        interview = await self._lock_session(session_id)
        if interview.status == "COMPLETED" or interview.current_stage == "COMPLETED":
            return None
        if self._clock() < interview.deadline_at:
            return None
        return await self.complete(
            session_id=session_id,
            reason="TIME_EXPIRED",
            expected_state_version=interview.state_version,
            idempotency_key=f"deadline-reconciliation:{session_id}",
        )

    async def _lock_session(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update(),
        )
        if interview is None:
            raise SessionNotFound(f"InterviewSession not found: {session_id}")
        return interview

    async def _terminalize_prompt_lifecycle(self, session_id: UUID) -> None:
        deliveries = list(
            (
                await self._session.scalars(
                    select(InterviewerPromptDelivery)
                    .options(selectinload(InterviewerPromptDelivery.interviewer_prompt))
                    .where(InterviewerPromptDelivery.interview_session_id == session_id)
                    .where(InterviewerPromptDelivery.delivery_state == "STARTED")
                    .with_for_update(),
                )
            ).all()
        )
        for delivery in deliveries:
            delivery.delivery_state = "INTERRUPTED"
            delivery.interrupted_at = self._clock()
            delivery.interviewer_prompt.status = "INTERRUPTED"

        prompts = list(
            (
                await self._session.scalars(
                    select(InterviewerPrompt)
                    .where(InterviewerPrompt.interview_session_id == session_id)
                    .where(InterviewerPrompt.status == "AUTHORIZED"),
                )
            ).all()
        )
        # Prompts without a delivery are intent only and must never become audible later.
        for prompt in prompts:
            if prompt.status == "AUTHORIZED":
                prompt.status = "CANCELLED"
        await self._session.flush()

    async def _terminal_reason(self, session_id: UUID) -> TerminalReason:
        transition = await self._session.scalar(
            select(InterviewStageTransition)
            .where(InterviewStageTransition.interview_session_id == session_id)
            .where(InterviewStageTransition.to_stage == "WRAP_UP")
            .order_by(InterviewStageTransition.state_version.desc())
            .limit(1),
        )
        return (
            "TIME_EXPIRED"
            if transition and transition.trigger == "HARD_TIME_CONTROL"
            else "USER_ENDED"
        )

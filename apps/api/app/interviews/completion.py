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

from app.db.ids import uuid7
from app.interviews.models import (
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    InterviewStageTransition,
)
from app.interviews.runtime import InterviewRuntime, SessionNotFound, TransitionCommand
from app.interviews.state_machine import TransitionContext
from app.observation.models import InterviewEvent
from app.outbox.repository import OutboxRepository

TerminalReason = Literal["USER_ENDED", "TIME_EXPIRED"]


class DeadlineNotReached(ValueError):
    """A client cannot complete an interview for time before server time expires."""


@dataclass(frozen=True)
class CompletionResult:
    interview: InterviewSession
    terminal_reason: TerminalReason
    created: bool
    transitions: tuple[InterviewStageTransition, ...]
    session_completed_event_id: UUID


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
            terminal_reason = await self._terminal_reason(interview.id)
            completion_event = await self._ensure_post_session_work(interview, terminal_reason)
            return CompletionResult(
                interview=interview,
                terminal_reason=terminal_reason,
                created=False,
                transitions=(),
                session_completed_event_id=completion_event.id,
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
        completion_event = await self._ensure_post_session_work(interview, reason)
        return CompletionResult(
            interview=interview,
            terminal_reason=reason,
            created=True,
            transitions=tuple(transitions),
            session_completed_event_id=completion_event.id,
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

    async def _ensure_post_session_work(
        self,
        interview: InterviewSession,
        reason: TerminalReason,
    ) -> InterviewEvent:
        completion_event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == interview.id)
            .where(InterviewEvent.event_type == "SESSION_COMPLETED")
            .order_by(InterviewEvent.server_sequence)
            .limit(1)
        )
        if completion_event is None:
            interview.last_server_sequence += 1
            occurred_at = interview.completed_at or self._clock()
            completion_event = InterviewEvent(
                id=uuid7(),
                interview_session_id=interview.id,
                user_id=interview.user_id,
                event_type="SESSION_COMPLETED",
                source="INTERVIEW_ORCHESTRATOR",
                occurred_at=occurred_at,
                received_at=self._clock(),
                server_sequence=interview.last_server_sequence,
                interview_state_version=interview.state_version,
                causation_id=None,
                correlation_id=None,
                code_snapshot_id=None,
                idempotency_key=f"session-completed:{interview.id}",
                payload={"terminal_reason": reason},
                provenance={"completion_policy": "interview-completion.v1"},
                schema_version="session.completed.v1",
            )
            self._session.add(completion_event)
            await self._session.flush()
        await OutboxRepository(self._session).enqueue(
            aggregate_type="InterviewSession",
            aggregate_id=interview.id,
            interview_session_id=interview.id,
            event_type="FINALIZE_SESSION_EVIDENCE",
            payload={
                "interview_session_id": str(interview.id),
                "assessment_policy": "assessment_evaluator.v3",
                "completion_event_id": str(completion_event.id),
            },
            deduplication_key=(f"evidence-finalization:{interview.id}:assessment_evaluator.v3"),
            available_at=self._clock(),
            source_watermark=completion_event.server_sequence,
        )
        return completion_event

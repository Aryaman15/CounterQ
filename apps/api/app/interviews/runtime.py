from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ids import uuid7
from app.interviews.models import (
    InterviewerPromptDelivery,
    InterviewSession,
    InterviewStageTransition,
)
from app.interviews.state_machine import (
    STATE_MACHINE_POLICY_VERSION,
    TransitionContext,
    require_transition,
    transition_may_bypass_active_delivery_guard,
)
from app.observation.models import InterviewEvent

logger = structlog.get_logger(__name__)


class InterviewRuntimeError(ValueError):
    pass


class SessionNotFound(InterviewRuntimeError):
    pass


class SessionClosed(InterviewRuntimeError):
    pass


class SessionDeadlineReached(InterviewRuntimeError):
    pass


class StaleStateVersion(InterviewRuntimeError):
    pass


class IdempotencyConflict(InterviewRuntimeError):
    pass


class ActivePromptDeliveryBlocksTransition(InterviewRuntimeError):
    pass


@dataclass(frozen=True)
class AcceptEventCommand:
    session_id: UUID
    event_type: str
    source: str
    occurred_at: datetime
    idempotency_key: str | None = None
    payload: dict[str, object] | None = None
    provenance: dict[str, object] | None = None
    schema_version: str = "interview.event.v1"
    expected_state_version: int | None = None
    client_instance_id: str | None = None
    client_sequence: int | None = None
    causation_id: UUID | None = None
    correlation_id: UUID | None = None
    code_snapshot_id: UUID | None = None


@dataclass(frozen=True)
class AcceptedEvent:
    event: InterviewEvent
    created: bool


@dataclass(frozen=True)
class TransitionCommand:
    session_id: UUID
    to_stage: str
    trigger: str
    expected_state_version: int
    occurred_at: datetime
    idempotency_key: str | None = None
    context: TransitionContext | None = None
    transition_policy_version: str = STATE_MACHINE_POLICY_VERSION


class InterviewRuntime:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
        before_transition_flush: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self._before_transition_flush = before_transition_flush

    async def accept_event(self, command: AcceptEventCommand) -> AcceptedEvent:
        interview = await self._lock_session(command.session_id)
        existing = await self._find_idempotent_event(interview.id, command.idempotency_key)
        if existing is not None:
            self._ensure_idempotent_match(existing, command)
            return AcceptedEvent(event=existing, created=False)

        self._ensure_session_accepts_activity(interview)
        self._ensure_expected_version(interview, command.expected_state_version)

        event = self._build_event(
            interview=interview,
            command=command,
            received_at=self._clock(),
            interview_state_version=interview.state_version,
        )
        self._session.add(event)
        await self._session.flush()
        logger.info(
            "interview_event_accepted",
            session_id=str(interview.id),
            event_id=str(event.id),
            server_sequence=event.server_sequence,
            state_version=event.interview_state_version,
        )
        return AcceptedEvent(event=event, created=True)

    async def transition(self, command: TransitionCommand) -> InterviewStageTransition:
        interview = await self._lock_session(command.session_id)
        existing = await self._find_idempotent_event(interview.id, command.idempotency_key)
        if existing is not None:
            transition = await self._session.scalar(
                select(InterviewStageTransition).where(
                    InterviewStageTransition.event_id == existing.id,
                ),
            )
            if transition is None:
                raise IdempotencyConflict(
                    "Transition idempotency key belongs to a non-transition event"
                )
            self._ensure_idempotent_transition_match(transition, command)
            return transition

        self._ensure_session_accepts_activity(
            interview, allow_completion=command.to_stage == "COMPLETED"
        )
        self._ensure_expected_version(interview, command.expected_state_version)
        context = command.context or TransitionContext(trigger=command.trigger)
        require_transition(interview.current_stage, command.to_stage, context)
        await self._ensure_transition_not_ambiguous_with_active_delivery(interview.id, context)

        new_state_version = interview.state_version + 1
        payload: dict[str, object] = {
            "from_stage": interview.current_stage,
            "to_stage": command.to_stage,
            "trigger": command.trigger,
        }
        event_command = AcceptEventCommand(
            session_id=interview.id,
            event_type="STAGE_CHANGED",
            source="INTERVIEW_ORCHESTRATOR",
            occurred_at=command.occurred_at,
            idempotency_key=command.idempotency_key,
            payload=payload,
            provenance={"transition_policy_version": command.transition_policy_version},
            schema_version="interview.stage_transition.v1",
        )
        event = self._build_event(
            interview=interview,
            command=event_command,
            received_at=self._clock(),
            interview_state_version=new_state_version,
        )
        transition = InterviewStageTransition(
            interview_session_id=interview.id,
            from_stage=interview.current_stage,
            to_stage=command.to_stage,
            state_version=new_state_version,
            trigger=command.trigger,
            occurred_at=command.occurred_at,
            event_id=event.id,
            transition_policy_version=command.transition_policy_version,
        )
        interview.current_stage = command.to_stage
        interview.state_version = new_state_version
        if command.to_stage == "COMPLETED":
            interview.status = "COMPLETED"
            interview.completed_at = self._clock()
        self._session.add_all([event, transition])
        if self._before_transition_flush is not None:
            self._before_transition_flush()
        await self._session.flush()
        logger.info(
            "interview_stage_transitioned",
            session_id=str(interview.id),
            event_id=str(event.id),
            transition_id=str(transition.id),
            server_sequence=event.server_sequence,
            state_version=new_state_version,
        )
        return transition

    async def complete_interview(
        self,
        *,
        session_id: UUID,
        expected_state_version: int,
        occurred_at: datetime,
        idempotency_key: str | None = None,
    ) -> InterviewStageTransition:
        return await self.transition(
            TransitionCommand(
                session_id=session_id,
                to_stage="COMPLETED",
                trigger="CLOSING_COMPLETE",
                expected_state_version=expected_state_version,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
            ),
        )

    async def _lock_session(self, session_id: UUID) -> InterviewSession:
        interview = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == session_id).with_for_update(),
        )
        if interview is None:
            raise SessionNotFound(f"InterviewSession not found: {session_id}")
        return interview

    async def _find_idempotent_event(
        self,
        session_id: UUID,
        idempotency_key: str | None,
    ) -> InterviewEvent | None:
        if idempotency_key is None:
            return None
        event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == session_id)
            .where(InterviewEvent.idempotency_key == idempotency_key),
        )
        return cast(InterviewEvent | None, event)

    def _build_event(
        self,
        *,
        interview: InterviewSession,
        command: AcceptEventCommand,
        received_at: datetime,
        interview_state_version: int,
    ) -> InterviewEvent:
        interview.last_server_sequence += 1
        return InterviewEvent(
            id=uuid7(),
            interview_session_id=interview.id,
            user_id=interview.user_id,
            event_type=command.event_type,
            source=command.source,
            occurred_at=command.occurred_at,
            received_at=received_at,
            client_instance_id=command.client_instance_id,
            client_sequence=command.client_sequence,
            server_sequence=interview.last_server_sequence,
            interview_state_version=interview_state_version,
            causation_id=command.causation_id,
            correlation_id=command.correlation_id,
            code_snapshot_id=command.code_snapshot_id,
            idempotency_key=command.idempotency_key,
            payload=command.payload or {},
            provenance=command.provenance or {},
            schema_version=command.schema_version,
        )

    def _ensure_session_accepts_activity(
        self,
        interview: InterviewSession,
        *,
        allow_completion: bool = False,
    ) -> None:
        if interview.status in {"COMPLETED", "ABANDONED", "DELETION_PENDING"}:
            raise SessionClosed(f"InterviewSession is closed: {interview.id}")
        if interview.current_stage == "COMPLETED":
            raise SessionClosed(f"InterviewSession is completed: {interview.id}")
        if self._clock() >= interview.deadline_at and not allow_completion:
            raise SessionDeadlineReached(f"InterviewSession deadline reached: {interview.id}")

    @staticmethod
    def _ensure_expected_version(
        interview: InterviewSession,
        expected_state_version: int | None,
    ) -> None:
        if expected_state_version is not None and expected_state_version != interview.state_version:
            raise StaleStateVersion(
                f"Expected state_version {expected_state_version}; "
                f"current is {interview.state_version}"
            )

    @staticmethod
    def _ensure_idempotent_match(existing: InterviewEvent, command: AcceptEventCommand) -> None:
        if (
            existing.event_type != command.event_type
            or existing.source != command.source
            or existing.payload != (command.payload or {})
            or existing.schema_version != command.schema_version
            or existing.code_snapshot_id != command.code_snapshot_id
        ):
            raise IdempotencyConflict("Idempotency key conflicts with existing accepted event")

    @staticmethod
    def _ensure_idempotent_transition_match(
        existing: InterviewStageTransition,
        command: TransitionCommand,
    ) -> None:
        if (
            existing.to_stage != command.to_stage
            or existing.trigger != command.trigger
            or existing.transition_policy_version != command.transition_policy_version
        ):
            raise IdempotencyConflict("Transition idempotency key conflicts with existing history")

    async def _ensure_transition_not_ambiguous_with_active_delivery(
        self,
        interview_session_id: UUID,
        context: TransitionContext,
    ) -> None:
        if transition_may_bypass_active_delivery_guard(context):
            return
        active_delivery_id = await self._session.scalar(
            select(InterviewerPromptDelivery.id)
            .where(InterviewerPromptDelivery.interview_session_id == interview_session_id)
            .where(InterviewerPromptDelivery.delivery_state == "STARTED")
            .limit(1),
        )
        if active_delivery_id is not None:
            raise ActivePromptDeliveryBlocksTransition(
                "Normal stage transition cannot proceed while a PromptDelivery is active"
            )

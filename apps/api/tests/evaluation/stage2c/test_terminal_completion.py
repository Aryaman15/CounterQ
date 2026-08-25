"""Deterministic Stage 2C terminal lifecycle acceptance coverage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.completion import DeadlineNotReached, InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.models import InterviewStageTransition
from app.interviews.restoration import SessionRestorationService
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime, SessionClosed


def fixed_clock(now: datetime) -> Callable[[], datetime]:
    return lambda: now


async def test_candidate_finish_creates_wrap_up_then_completed_once(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")

    service = InterviewCompletionService(db_session, clock=fixed_clock(now))
    result = await service.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage2c-user-finish",
    )
    assert result.created is True
    assert [transition.to_stage for transition in result.transitions] == ["WRAP_UP", "COMPLETED"]
    assert development.interview_session.status == "COMPLETED"
    assert development.interview_session.current_stage == "COMPLETED"
    assert development.interview_session.state_version == 2
    assert development.interview_session.last_server_sequence == 2
    assert development.interview_session.completed_at == now

    retry = await service.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=2,
        idempotency_key="stage2c-user-finish",
    )
    assert retry.created is False
    assert development.interview_session.state_version == 2
    assert development.interview_session.last_server_sequence == 2


async def test_timeout_requires_server_deadline_and_reconciles_active_delivery(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    prompt = await InterviewInteractionRepository(db_session).add_prompt(
        interview_session_id=development.interview_session.id,
        origin="SYSTEM",
        kind="INSTRUCTION",
        intent="Never candidate visible without delivery.",
        status="AUTHORIZED",
    )
    delivery = await InterviewInteractionRepository(db_session).add_delivery(
        interview_session_id=development.interview_session.id,
        interviewer_prompt_id=prompt.id,
        delivery_attempt=1,
        intended_text="Never candidate visible without delivery.",
        delivery_state="STARTED",
        started_at=now,
    )
    early = InterviewCompletionService(db_session, clock=fixed_clock(now))
    with pytest.raises(DeadlineNotReached):
        await early.complete(
            session_id=development.interview_session.id,
            reason="TIME_EXPIRED",
            expected_state_version=0,
            idempotency_key="stage2c-early-timeout",
        )
    assert development.interview_session.status == "ACTIVE"

    result = await InterviewCompletionService(
        db_session, clock=fixed_clock(development.interview_session.deadline_at)
    ).complete(
        session_id=development.interview_session.id,
        reason="TIME_EXPIRED",
        expected_state_version=0,
        idempotency_key="stage2c-timeout",
    )
    assert result.terminal_reason == "TIME_EXPIRED"
    assert delivery.delivery_state == "INTERRUPTED"
    assert delivery.actual_transcript_segment_id is None
    assert prompt.status == "INTERRUPTED"
    assert development.budget.probes_used == 0


async def test_completed_session_restores_same_terminal_truth_and_rejects_activity(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    development = await create_development_interview(db_session, initial_stage="WRAP_UP")
    await InterviewCompletionService(db_session, clock=fixed_clock(now)).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage2c-wrap-only",
    )
    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=development.interview_session.id,
        client_instance_id="stage2c-client",
    )
    assert restored.interview.id == development.interview_session.id
    assert restored.interview.status == "COMPLETED"
    assert restored.terminal_reason == "USER_ENDED"
    assert restored.interview.completed_at == now
    with pytest.raises(SessionClosed):
        await InterviewRuntime(db_session, clock=fixed_clock(now)).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="TRANSCRIPT_FINALIZED",
                source="CANDIDATE_VOICE",
                occurred_at=now,
                idempotency_key="stage2c-after-complete",
            )
        )


async def test_expired_active_restore_completes_before_projection(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    development.interview_session.started_at = now - timedelta(seconds=2)
    development.interview_session.deadline_at = now - timedelta(seconds=1)
    await db_session.flush()
    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=development.interview_session.id,
        client_instance_id="stage2c-expired-client",
    )
    transitions = list(
        (
            await db_session.scalars(
                select(InterviewStageTransition)
                .where(
                    InterviewStageTransition.interview_session_id
                    == development.interview_session.id
                )
                .order_by(InterviewStageTransition.state_version),
            )
        ).all()
    )
    assert restored.interview.status == "COMPLETED"
    assert restored.terminal_reason == "TIME_EXPIRED"
    assert [transition.to_stage for transition in transitions] == ["WRAP_UP", "COMPLETED"]

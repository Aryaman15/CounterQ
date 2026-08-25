"""Adversarial acceptance coverage for the complete Stage 2 durable core."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewStageTransition
from app.interviews.restoration import SessionRestorationService
from app.interviews.runtime import (
    AcceptEventCommand,
    InterviewRuntime,
    SessionClosed,
    TransitionCommand,
)
from app.interviews.state_machine import TransitionContext
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
)
from app.realtime.control_service import RealtimeControlService


def clock_at(now: datetime) -> Callable[[], datetime]:
    return lambda: now


async def test_active_refresh_retry_and_process_reconstruction_preserve_canonical_truth(
    db_session: AsyncSession,
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    development.interview_session.started_at = now
    development.interview_session.deadline_at = now + timedelta(minutes=30)
    await db_session.flush()
    service = RealtimeControlService(db_session, clock=clock_at(now + timedelta(minutes=3)))
    transcript_message = CandidateTranscriptFinalizedMessage(
        type="candidate_transcript_finalized",
        client_event_id="transcript-1",
        client_instance_id="browser-a",
        client_sequence=1,
        provider_item_id="provider-1",
        transcript="I will use a map.",
        idempotency_key="stage2d-transcript-1",
    )
    first = await service.persist_candidate_transcript(
        session_id=development.interview_session.id,
        message=transcript_message,
    )
    retry = await RealtimeControlService(
        db_session, clock=clock_at(now + timedelta(minutes=3))
    ).persist_candidate_transcript(
        session_id=development.interview_session.id,
        message=transcript_message,
    )
    assert retry.created is False
    assert retry.event_id == first.event_id
    assert retry.server_sequence == first.server_sequence

    code_message = CandidateCodeSnapshotMessage(
        type="candidate_code_snapshot",
        client_event_id="code-1",
        client_instance_id="browser-a",
        client_sequence=2,
        source_code="class Solution {};",
        language="cpp",
        trigger="INITIAL_EDITOR_STATE",
        idempotency_key="stage2d-code-1",
    )
    code = await service.persist_candidate_code_snapshot(
        session_id=development.interview_session.id,
        message=code_message,
    )
    code_retry = await RealtimeControlService(db_session).persist_candidate_code_snapshot(
        session_id=development.interview_session.id,
        message=code_message,
    )
    assert code_retry.created is False
    assert code_retry.snapshot_id == code.snapshot_id

    runtime = InterviewRuntime(
        db_session,
        clock=clock_at(now + timedelta(minutes=4)),
    )
    transition = await runtime.transition(
        TransitionCommand(
            session_id=development.interview_session.id,
            to_stage="TESTING_DEBUGGING",
            trigger="MEANINGFUL_TESTING",
            expected_state_version=0,
            occurred_at=now + timedelta(minutes=4),
            idempotency_key="stage2d-testing",
            context=TransitionContext("MEANINGFUL_TESTING"),
        )
    )
    restored = await SessionRestorationService(
        db_session, clock=clock_at(now + timedelta(minutes=5))
    ).restore(
        interview_session_id=development.interview_session.id,
        client_instance_id="browser-a",
    )
    assert restored.interview.id == development.interview_session.id
    assert restored.interview.started_at == now
    assert restored.interview.deadline_at == now + timedelta(minutes=30)
    assert restored.interview.current_stage == "TESTING_DEBUGGING"
    assert restored.interview.state_version == transition.state_version
    assert restored.code_snapshot and restored.code_snapshot.id == code.snapshot_id
    assert [turn.text for turn in restored.conversation] == ["I will use a map."]
    assert restored.highest_client_sequence == 2
    assert development.interview_session.last_server_sequence == 3


async def test_terminal_boundaries_are_idempotent_and_immutable(db_session: AsyncSession) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    development.interview_session.started_at = now
    development.interview_session.deadline_at = now + timedelta(minutes=30)
    await db_session.flush()
    completion = InterviewCompletionService(db_session, clock=clock_at(now + timedelta(minutes=4)))
    first = await completion.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage2d-end",
    )
    completion_retry = InterviewCompletionService(
        db_session,
        clock=clock_at(now + timedelta(minutes=5)),
    )
    retry = await completion_retry.complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=2,
        idempotency_key="stage2d-end",
    )
    transitions = list(
        (
            await db_session.scalars(
                select(InterviewStageTransition)
                .where(
                    InterviewStageTransition.interview_session_id
                    == development.interview_session.id
                )
                .order_by(InterviewStageTransition.state_version)
            )
        ).all()
    )
    assert first.created is True and retry.created is False
    assert [row.to_stage for row in transitions] == ["WRAP_UP", "COMPLETED"]
    assert development.interview_session.completed_at == now + timedelta(minutes=4)
    assert development.interview_session.last_server_sequence == 2
    assert development.interview_session.state_version == 2
    with pytest.raises(SessionClosed):
        await InterviewRuntime(db_session, clock=clock_at(now + timedelta(minutes=5))).accept_event(
            AcceptEventCommand(
                session_id=development.interview_session.id,
                event_type="TRANSCRIPT_FINALIZED",
                source="CANDIDATE_VOICE",
                occurred_at=now,
                idempotency_key="stage2d-terminal-mutation",
            )
        )
    with pytest.raises(SessionClosed):
        terminal_control = RealtimeControlService(
            db_session,
            clock=clock_at(now + timedelta(minutes=5)),
        )
        await terminal_control.create_development_authorized_prompt(
            session_id=development.interview_session.id
        )
    terminal_restore = SessionRestorationService(
        db_session,
        clock=clock_at(now + timedelta(minutes=5)),
    )
    restored = await terminal_restore.restore(
        interview_session_id=development.interview_session.id,
        client_instance_id="browser-b",
    )
    assert restored.interview.status == "COMPLETED"
    assert restored.terminal_reason == "USER_ENDED"


async def test_deadline_reconciliation_wins_terminal_race_without_duplicate_history(
    db_session: AsyncSession,
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    development.interview_session.started_at = now - timedelta(minutes=30)
    development.interview_session.deadline_at = now
    await db_session.flush()
    result = await InterviewCompletionService(db_session, clock=clock_at(now)).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="stage2d-race-user",
    )
    timeout_retry = await InterviewCompletionService(
        db_session, clock=clock_at(now + timedelta(seconds=1))
    ).complete(
        session_id=development.interview_session.id,
        reason="TIME_EXPIRED",
        expected_state_version=2,
        idempotency_key="stage2d-race-timeout",
    )
    transition_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewStageTransition)
        .where(InterviewStageTransition.interview_session_id == development.interview_session.id)
    )
    assert result.terminal_reason == "TIME_EXPIRED"
    assert timeout_retry.created is False
    assert timeout_retry.terminal_reason == "TIME_EXPIRED"
    assert transition_count == 2
    assert development.interview_session.status == "COMPLETED"

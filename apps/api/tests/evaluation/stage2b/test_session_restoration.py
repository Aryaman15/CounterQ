from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.dev_factory import DevelopmentInterview, create_development_interview
from app.interviews.models import InterviewerPromptDelivery
from app.interviews.restoration import SessionRestorationService
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryStartedMessage,
)
from app.realtime.control_service import RealtimeControlService


def client_base(sequence: int) -> dict[str, object]:
    return {
        "client_event_id": f"restore-client-event-{sequence}",
        "client_instance_id": "restore-browser-tab",
        "client_sequence": sequence,
    }


async def _interview(db_session: AsyncSession) -> DevelopmentInterview:
    return await create_development_interview(db_session, initial_stage="IMPLEMENTATION")


async def test_restore_reconstructs_same_session_deadline_stage_budget_and_latest_code(
    db_session: AsyncSession,
) -> None:
    dev = await _interview(db_session)
    source = "class Solution { public: int lengthOfLongestSubstring(string s) { return 7; } };"
    result = await RealtimeControlService(db_session).persist_candidate_code_snapshot(
        session_id=dev.interview_session.id,
        message=CandidateCodeSnapshotMessage(
            **client_base(1),
            type="candidate_code_snapshot",
            source_code=source,
            language="cpp",
            trigger="EDIT_BURST",
            idempotency_key="restore-code-1",
        ),
    )
    dev.budget.probes_used = 1
    await db_session.flush()
    session_id = dev.interview_session.id
    deadline_at = dev.interview_session.deadline_at
    # Prove the restore projection does not rely on an in-memory aggregate.
    db_session.expunge_all()

    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=session_id,
        client_instance_id="restore-browser-tab",
    )

    assert restored.interview.id == session_id
    assert restored.interview.deadline_at == deadline_at
    assert restored.interview.current_stage == "IMPLEMENTATION"
    assert restored.interview.state_version == 0
    assert restored.interview.budget is not None and restored.interview.budget.probes_used == 1
    assert restored.code_snapshot is not None
    assert restored.code_snapshot.id == result.snapshot_id
    assert restored.code_snapshot.source_code == source
    assert restored.highest_client_sequence == 1


async def test_restore_exposes_only_finalized_candidate_and_actual_delivered_conversation(
    db_session: AsyncSession,
) -> None:
    dev = await _interview(db_session)
    service = RealtimeControlService(db_session)
    await service.persist_candidate_transcript(
        session_id=dev.interview_session.id,
        message=CandidateTranscriptFinalizedMessage(
            **client_base(1),
            type="candidate_transcript_finalized",
            provider_item_id="candidate-turn-1",
            transcript="I will use a sliding window.",
            idempotency_key="restore-transcript-1",
        ),
    )
    authorized = await service.create_development_authorized_prompt(
        session_id=dev.interview_session.id
    )
    started = await service.start_delivery(
        session_id=dev.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            **client_base(2),
            type="counterq_delivery_started",
            interviewer_prompt_id=authorized.prompt_id,
            intended_text=authorized.text,
            provider_response_id="response-restore-1",
        ),
    )
    await service.complete_delivery(
        session_id=dev.interview_session.id,
        message=CounterQDeliveryCompletedMessage(
            **client_base(3),
            type="counterq_delivery_completed",
            interviewer_prompt_id=authorized.prompt_id,
            prompt_delivery_id=started.delivery_id,
            provider_response_id="response-restore-1",
            transcript="Walk me through the approach you're considering.",
            idempotency_key="restore-delivery-1",
        ),
    )
    await service.create_development_authorized_prompt(
        session_id=dev.interview_session.id,
    )

    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=dev.interview_session.id,
        client_instance_id="restore-browser-tab",
    )

    assert [(turn.speaker, turn.text) for turn in restored.conversation] == [
        ("CANDIDATE", "I will use a sliding window."),
        ("COUNTERQ", "Walk me through the approach you're considering."),
    ]
    assert restored.unresolved_prompt is not None
    assert restored.unresolved_prompt.id != authorized.prompt_id
    assert all("intended" not in turn.text.lower() for turn in restored.conversation)


async def test_restore_reconciles_an_orphaned_started_delivery_without_fabricating_text(
    db_session: AsyncSession,
) -> None:
    dev = await _interview(db_session)
    service = RealtimeControlService(db_session)
    authorized = await service.create_development_authorized_prompt(
        session_id=dev.interview_session.id
    )
    started = await service.start_delivery(
        session_id=dev.interview_session.id,
        message=CounterQDeliveryStartedMessage(
            **client_base(1),
            type="counterq_delivery_started",
            interviewer_prompt_id=authorized.prompt_id,
            intended_text=authorized.text,
            provider_response_id="response-orphaned-1",
            started_at=datetime.now(UTC),
        ),
    )

    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=dev.interview_session.id,
        client_instance_id="restore-browser-tab",
        reconcile_orphaned_deliveries=True,
    )
    delivery = await db_session.scalar(
        select(InterviewerPromptDelivery).where(
            InterviewerPromptDelivery.id == started.delivery_id
        ),
    )

    assert delivery is not None and delivery.delivery_state == "INTERRUPTED"
    assert delivery.actual_transcript_segment_id is None
    assert restored.conversation == ()
    assert dev.budget.probes_used == 0


async def test_restore_itself_does_not_create_events_or_duplicate_an_idempotent_retry(
    db_session: AsyncSession,
) -> None:
    dev = await _interview(db_session)
    service = RealtimeControlService(db_session)
    message = CandidateTranscriptFinalizedMessage(
        **client_base(1),
        type="candidate_transcript_finalized",
        provider_item_id="candidate-retry-1",
        transcript="One durable turn.",
        idempotency_key="restore-retry-1",
    )
    first = await service.persist_candidate_transcript(
        session_id=dev.interview_session.id,
        message=message,
    )
    retry = await service.persist_candidate_transcript(
        session_id=dev.interview_session.id,
        message=message,
    )
    before_sequence = dev.interview_session.last_server_sequence

    restored = await SessionRestorationService(db_session).restore(
        interview_session_id=dev.interview_session.id,
        client_instance_id="restore-browser-tab",
    )

    assert first.event_id == retry.event_id
    assert retry.created is False
    assert restored.interview.last_server_sequence == before_sequence == 1
    assert len(restored.conversation) == 1

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import create_settings, get_settings
from app.db.session import get_session
from app.interviews.dev_factory import create_development_interview
from app.interviews.models import InterviewerPrompt, InterviewerPromptDelivery, InterviewSession
from app.interviews.runtime import IdempotencyConflict
from app.main import create_app
from app.observation.models import InterviewEvent, TranscriptSegment
from app.realtime.control_protocol import (
    CandidateTranscriptFinalizedMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
    DevelopmentAuthorizedPromptRequestMessage,
    RealtimeDevelopmentBootstrapRequest,
    RealtimeDisconnectedMessage,
    RealtimeReconnectedMessage,
)
from app.realtime.control_service import (
    DEVELOPMENT_DELIVERY_VALIDATION_INTENT,
    DEVELOPMENT_DELIVERY_VALIDATION_TEXT,
    RealtimeControlService,
    RealtimeControlSessionNotFound,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def client_base(sequence: int = 1) -> dict[str, object]:
    return {
        "client_event_id": f"client-event-{sequence}",
        "client_instance_id": "client-tab-1",
        "client_sequence": sequence,
    }


async def dev_session(db_session: AsyncSession) -> InterviewSession:
    dev = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    return dev.interview_session


async def test_development_bootstrap_factory_creates_real_persisted_interview(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)

    stored = await db_session.get(InterviewSession, interview.id)

    assert stored is not None
    assert stored.current_stage == "IMPLEMENTATION"
    assert stored.status == "ACTIVE"


@pytest.mark.asyncio
async def test_development_bootstrap_endpoint_is_blocked_outside_development(
    tmp_path: Path,
) -> None:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "production"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        result = await client.post(
            "/api/realtime/development-interview",
            json=RealtimeDevelopmentBootstrapRequest().model_dump(mode="json"),
        )

    assert result.status_code == 403
    assert result.json()["detail"]["category"] == "development_only"
    app.dependency_overrides.clear()


async def test_development_bootstrap_endpoint_returns_persisted_interview(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        result = await client.post(
            "/api/realtime/development-interview",
            json=RealtimeDevelopmentBootstrapRequest().model_dump(mode="json"),
        )

    assert result.status_code == 200
    payload = result.json()
    stored = await db_session.get(InterviewSession, payload["interview_session_id"])
    assert stored is not None
    assert stored.current_stage == "IMPLEMENTATION"
    assert payload["control_websocket_path"].endswith(payload["interview_session_id"])
    app.dependency_overrides.clear()


def test_control_websocket_accepts_client_hello_after_server_ready(tmp_path: Path) -> None:
    settings = create_settings(env_file=tmp_path / ".env")
    settings.app_env = "local"
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        bootstrap = client.post(
            "/api/realtime/development-interview",
            json=RealtimeDevelopmentBootstrapRequest().model_dump(mode="json"),
        )
        assert bootstrap.status_code == 200
        control_path = bootstrap.json()["control_websocket_path"]

        with client.websocket_connect(control_path) as websocket:
            server_hello = websocket.receive_json()
            assert server_hello["type"] == "server_hello"

            websocket.send_json(
                {
                    "protocol_version": "counterq.realtime.control.v1",
                    "type": "client_hello",
                    "client_event_id": "client-hello-1",
                    "client_instance_id": "client-tab-1",
                    "client_sequence": 1,
                    "last_acknowledged_server_sequence": 0,
                },
            )
            ack = websocket.receive_json()

    assert ack["type"] == "control_signal_ack"
    assert ack["client_event_id"] == "client-hello-1"
    assert ack["floor_state"] == "IDLE"
    app.dependency_overrides.clear()


async def test_control_service_validates_interview_session_exists(db_session: AsyncSession) -> None:
    service = RealtimeControlService(db_session)

    with pytest.raises(RealtimeControlSessionNotFound):
        await service.ensure_session_exists(uuid4())


async def test_candidate_final_transcript_persists_one_event_and_segment(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    message = CandidateTranscriptFinalizedMessage(
        **client_base(),
        type="candidate_transcript_finalized",
        provider_item_id="item-candidate-1",
        content_index=0,
        transcript="I am using a sliding window.",
        ended_at=utcnow(),
    )

    result = await RealtimeControlService(db_session).persist_candidate_transcript(
        session_id=interview.id,
        message=message,
    )

    event = await db_session.get(InterviewEvent, result.event_id)
    segment = await db_session.get(TranscriptSegment, result.transcript_segment_id)
    assert event is not None
    assert segment is not None
    assert event.event_type == "TRANSCRIPT_FINALIZED"
    assert event.source == "CANDIDATE_VOICE"
    assert event.payload == {"provider_segment_id": "item-candidate-1", "content_index": 0}
    assert segment.speaker == "CANDIDATE"
    assert segment.text == "I am using a sliding window."
    assert segment.provider_segment_id == "item-candidate-1"
    assert segment.interview_stage == "IMPLEMENTATION"
    assert segment.interview_state_version == interview.state_version
    assert segment.sequence == event.server_sequence == 1


async def test_candidate_final_transcript_is_idempotent_and_conflict_checked(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    message = CandidateTranscriptFinalizedMessage(
        **client_base(),
        type="candidate_transcript_finalized",
        provider_item_id="item-retry",
        transcript="Final transcript once.",
    )

    first = await service.persist_candidate_transcript(session_id=interview.id, message=message)
    retry = await service.persist_candidate_transcript(session_id=interview.id, message=message)

    assert first.created is True
    assert retry.created is False
    assert retry.event_id == first.event_id
    assert retry.transcript_segment_id == first.transcript_segment_id
    assert interview.last_server_sequence == 1
    segment_count = await db_session.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.interview_session_id == interview.id),
    )
    assert segment_count == 1

    with pytest.raises(IdempotencyConflict):
        await service.persist_candidate_transcript(
            session_id=interview.id,
            message=CandidateTranscriptFinalizedMessage(
                **client_base(2),
                type="candidate_transcript_finalized",
                provider_item_id="item-retry",
                transcript="Conflicting final transcript.",
            ),
        )


async def test_partial_transcript_control_signal_is_not_persisted(db_session: AsyncSession) -> None:
    interview = await dev_session(db_session)
    interview_id = interview.id
    service = RealtimeControlService(db_session)

    service.candidate_speech_started()
    service.candidate_speech_stopped()

    event_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewEvent)
        .where(InterviewEvent.interview_session_id == interview_id),
    )
    assert event_count == 0
    assert service.floor.state == "CANDIDATE_THINKING"


async def test_realtime_connectivity_events_persist_without_transcript_text(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)

    disconnected = await service.persist_realtime_connectivity_event(
        session_id=interview.id,
        message=RealtimeDisconnectedMessage(
            **client_base(),
            type="realtime_disconnected",
            provider_session_id="provider-session-1",
            reason="ice_failed",
        ),
    )
    reconnected = await service.persist_realtime_connectivity_event(
        session_id=interview.id,
        message=RealtimeReconnectedMessage(
            **client_base(2),
            type="realtime_reconnected",
            provider_session_id="provider-session-1",
        ),
    )

    first = await db_session.get(InterviewEvent, disconnected.event_id)
    second = await db_session.get(InterviewEvent, reconnected.event_id)
    assert first is not None
    assert second is not None
    assert first.event_type == "REALTIME_DISCONNECTED"
    assert second.event_type == "REALTIME_RECONNECTED"
    assert "transcript" not in str(first.payload).lower()
    assert [disconnected.server_sequence, reconnected.server_sequence] == [1, 2]


async def test_development_prompt_authorization_creates_no_delivery(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)

    result = await RealtimeControlService(db_session).create_development_authorized_prompt(
        session_id=interview.id,
    )

    prompt = await db_session.get(InterviewerPrompt, result.prompt_id)
    delivery_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewerPromptDelivery)
        .where(InterviewerPromptDelivery.interview_session_id == interview.id),
    )
    assert prompt is not None
    assert prompt.origin == "SYSTEM"
    assert prompt.kind == "INSTRUCTION"
    assert prompt.status == "AUTHORIZED"
    assert prompt.intent == DEVELOPMENT_DELIVERY_VALIDATION_INTENT
    assert result.text == DEVELOPMENT_DELIVERY_VALIDATION_TEXT
    assert delivery_count == 0


async def test_delivery_start_completion_and_interruption_persist_canonical_truth(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    prompt = await service.create_development_authorized_prompt(session_id=interview.id)

    start = await service.start_delivery(
        session_id=interview.id,
        message=CounterQDeliveryStartedMessage(
            **client_base(),
            type="counterq_delivery_started",
            interviewer_prompt_id=prompt.prompt_id,
            intended_text=prompt.text,
            provider_response_id="resp-1",
            provider_item_id="assistant-item-1",
            started_at=utcnow(),
        ),
    )

    assert start.delivery_state == "STARTED"
    assert service.floor.state == "COUNTERQ_SPEAKING"

    completed = await service.complete_delivery(
        session_id=interview.id,
        message=CounterQDeliveryCompletedMessage(
            **client_base(2),
            type="counterq_delivery_completed",
            interviewer_prompt_id=prompt.prompt_id,
            prompt_delivery_id=start.delivery_id,
            provider_response_id="resp-1",
            provider_item_id="assistant-item-1",
            transcript=prompt.text,
            completed_at=utcnow() + timedelta(seconds=2),
        ),
    )

    delivery = await db_session.get(InterviewerPromptDelivery, start.delivery_id)
    prompt_row = await db_session.get(InterviewerPrompt, prompt.prompt_id)
    segment = await db_session.get(TranscriptSegment, completed.transcript_segment_id)
    assert delivery is not None
    assert prompt_row is not None
    assert segment is not None
    assert delivery.delivery_state == "DELIVERED"
    assert delivery.actual_transcript_segment_id == segment.id
    assert prompt_row.status == "DELIVERED"
    assert segment.speaker == "COUNTERQ"
    assert segment.delivery_state == "DELIVERED"
    assert segment.text == prompt.text
    assert completed.server_sequence == 1

    second_prompt = await service.create_development_authorized_prompt(session_id=interview.id)
    second_start = await service.start_delivery(
        session_id=interview.id,
        message=CounterQDeliveryStartedMessage(
            **client_base(3),
            type="counterq_delivery_started",
            interviewer_prompt_id=second_prompt.prompt_id,
            intended_text=second_prompt.text,
            provider_response_id="resp-2",
        ),
    )
    interrupted = await service.interrupt_delivery(
        session_id=interview.id,
        message=CounterQDeliveryInterruptedMessage(
            **client_base(4),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=second_prompt.prompt_id,
            prompt_delivery_id=second_start.delivery_id,
            provider_response_id="resp-2",
            provider_item_id="assistant-item-2",
            confirmed_by="output_audio_buffer.cleared",
            audio_end_ms=900,
        ),
    )
    interrupted_delivery = await db_session.get(InterviewerPromptDelivery, second_start.delivery_id)
    assert interrupted_delivery is not None
    assert interrupted_delivery.delivery_state == "INTERRUPTED"
    assert interrupted_delivery.actual_transcript_segment_id is None
    assert interrupted.delivery_state == "INTERRUPTED"

    retry = await service.interrupt_delivery(
        session_id=interview.id,
        message=CounterQDeliveryInterruptedMessage(
            **client_base(5),
            type="counterq_delivery_interrupted",
            interviewer_prompt_id=second_prompt.prompt_id,
            prompt_delivery_id=second_start.delivery_id,
            provider_response_id="resp-2",
            provider_item_id="assistant-item-2",
            confirmed_by="output_audio_buffer.cleared",
            audio_end_ms=900,
        ),
    )
    assert retry.created is False
    assert retry.event_id == interrupted.event_id


async def test_realtime_control_transaction_rollback_leaves_no_orphan_segment(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    interview_id = interview.id
    service = RealtimeControlService(db_session)
    savepoint = await db_session.begin_nested()

    await service.persist_candidate_transcript(
        session_id=interview.id,
        message=CandidateTranscriptFinalizedMessage(
            **client_base(),
            type="candidate_transcript_finalized",
            provider_item_id="rollback-item",
            transcript="This will roll back.",
        ),
    )
    await savepoint.rollback()

    event_count = await db_session.scalar(
        select(func.count())
        .select_from(InterviewEvent)
        .where(InterviewEvent.interview_session_id == interview_id),
    )
    segment_count = await db_session.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.interview_session_id == interview_id),
    )
    assert event_count == 0
    assert segment_count == 0


def test_control_protocol_accepts_development_prompt_request() -> None:
    message = DevelopmentAuthorizedPromptRequestMessage(
        **client_base(),
        type="development_authorized_prompt_requested",
    )

    assert message.type == "development_authorized_prompt_requested"

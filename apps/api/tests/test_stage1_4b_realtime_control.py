from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
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
from app.observation.engine import ObservationEngine
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.realtime.control_protocol import (
    CandidateCodeSnapshotMessage,
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
    content_sha256,
    normalize_source_code,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def client_base(sequence: int = 1) -> dict[str, object]:
    return {
        "client_event_id": f"client-event-{sequence}",
        "client_instance_id": "client-tab-1",
        "client_sequence": sequence,
    }


def code_message(
    *,
    source_code: str,
    sequence: int = 1,
    trigger: Literal["INITIAL_EDITOR_STATE", "EDIT_BURST"] = "EDIT_BURST",
    idempotency_key: str | None = None,
) -> CandidateCodeSnapshotMessage:
    return CandidateCodeSnapshotMessage(
        **client_base(sequence),
        type="candidate_code_snapshot",
        source_code=source_code,
        language="cpp",
        trigger=trigger,
        idempotency_key=idempotency_key,
    )


CODE_RETURNS_ZERO = (
    "class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };"
)
CODE_RETURNS_ONE = (
    "class Solution { public: int lengthOfLongestSubstring(string s) { return 1; } };"
)
CODE_RETURNS_SIZE = (
    "class Solution { public: int lengthOfLongestSubstring(string s) { return s.size(); } };"
)
CODE_MULTILINE_ZERO = (
    "class Solution {\n"
    "public:\n"
    "    int lengthOfLongestSubstring(string s) { return 0; }\n"
    "};"
)
CODE_MULTILINE_SIZE = (
    "class Solution {\n"
    "public:\n"
    "    int lengthOfLongestSubstring(string s) { return s.size(); }\n"
    "};"
)


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


async def test_initial_code_snapshot_persists_event_snapshot_and_observation(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    source = CODE_MULTILINE_ZERO.replace("\n", "\r\n")

    result = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(
            source_code=source,
            trigger="INITIAL_EDITOR_STATE",
            idempotency_key="code-initial",
        ),
    )

    snapshot = await db_session.get(CodeSnapshot, result.snapshot_id)
    event = await db_session.get(InterviewEvent, result.event_id)
    assert snapshot is not None
    assert event is not None
    assert result.created is True
    assert result.diff_id is None
    assert snapshot.version_number == 1
    assert snapshot.parent_snapshot_id is None
    assert snapshot.source_code == normalize_source_code(source)
    assert snapshot.content_hash == content_sha256(normalize_source_code(source))
    assert event.event_type == "CODE_SNAPSHOT_CREATED"
    assert event.source == "NATIVE_EDITOR"
    assert event.code_snapshot_id == snapshot.id
    assert event.payload["content_hash"] == snapshot.content_hash
    assert event.payload["interview_stage"] == "IMPLEMENTATION"
    assert result.observation is not None
    assert result.observation.kind == "CODE_SNAPSHOT_CREATED"
    assert result.observation.code_snapshot_id == snapshot.id
    assert result.observation.code_source == snapshot.source_code


async def test_code_snapshot_idempotency_unchanged_and_conflict_rules(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    source = CODE_RETURNS_ZERO
    message = code_message(
        source_code=source,
        trigger="INITIAL_EDITOR_STATE",
        idempotency_key="code-stable",
    )

    first = await service.persist_candidate_code_snapshot(session_id=interview.id, message=message)
    retry = await service.persist_candidate_code_snapshot(session_id=interview.id, message=message)
    unchanged = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(source_code=source, sequence=2, idempotency_key="code-unchanged"),
    )

    assert first.created is True
    assert retry.created is False
    assert retry.event_id == first.event_id
    assert unchanged.created is False
    assert unchanged.snapshot_id == first.snapshot_id
    assert interview.last_server_sequence == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(CodeSnapshot)
        .where(CodeSnapshot.interview_session_id == interview.id),
    ) == 1
    with pytest.raises(IdempotencyConflict):
        await service.persist_candidate_code_snapshot(
            session_id=interview.id,
            message=code_message(
                source_code=CODE_RETURNS_ONE,
                trigger="INITIAL_EDITOR_STATE",
                idempotency_key="code-stable",
            ),
        )


async def test_code_edit_bursts_create_linear_versions_and_exact_diffs(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    source_a = CODE_MULTILINE_ZERO
    source_b = CODE_MULTILINE_SIZE

    first = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(
            source_code=source_a,
            sequence=1,
            trigger="INITIAL_EDITOR_STATE",
            idempotency_key="code-v1",
        ),
    )
    second = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(source_code=source_b, sequence=2, idempotency_key="code-v2"),
    )
    third = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(source_code=source_a, sequence=3, idempotency_key="code-v3"),
    )

    first_snapshot = await db_session.get(CodeSnapshot, first.snapshot_id)
    second_snapshot = await db_session.get(CodeSnapshot, second.snapshot_id)
    third_snapshot = await db_session.get(CodeSnapshot, third.snapshot_id)
    second_diff = await db_session.get(CodeDiff, second.diff_id)
    third_diff = await db_session.get(CodeDiff, third.diff_id)
    assert first_snapshot is not None
    assert second_snapshot is not None
    assert third_snapshot is not None
    assert second_diff is not None
    assert third_diff is not None
    assert [first.version_number, second.version_number, third.version_number] == [1, 2, 3]
    assert second_snapshot.parent_snapshot_id == first_snapshot.id
    assert third_snapshot.parent_snapshot_id == second_snapshot.id
    assert [first.server_sequence, second.server_sequence, third.server_sequence] == [1, 2, 3]
    assert second_diff.change_summary is None
    assert second_diff.significance is None
    assert "-    int lengthOfLongestSubstring(string s) { return 0; }" in second_diff.diff_content
    assert (
        "+    int lengthOfLongestSubstring(string s) { return s.size(); }"
        in second_diff.diff_content
    )


async def test_voice_observation_uses_code_snapshot_at_event_watermark(
    db_session: AsyncSession,
) -> None:
    interview = await dev_session(db_session)
    service = RealtimeControlService(db_session)
    first_code = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(
            source_code=CODE_RETURNS_ZERO,
            trigger="INITIAL_EDITOR_STATE",
            idempotency_key="watermark-code-v1",
        ),
    )
    transcript = await service.persist_candidate_transcript(
        session_id=interview.id,
        message=CandidateTranscriptFinalizedMessage(
            **client_base(2),
            type="candidate_transcript_finalized",
            provider_item_id="watermark-transcript",
            transcript="I think the window stays valid.",
        ),
    )
    second_code = await service.persist_candidate_code_snapshot(
        session_id=interview.id,
        message=code_message(
            source_code=CODE_RETURNS_SIZE,
            sequence=3,
            idempotency_key="watermark-code-v2",
        ),
    )

    observation = await ObservationEngine(db_session).project_event(transcript.event_id)

    assert second_code.version_number == 2
    assert observation.kind == "CANDIDATE_TRANSCRIPT_FINALIZED"
    assert observation.source_event_watermark == 2
    assert observation.transcript_segment_id == transcript.transcript_segment_id
    assert observation.associated_code_snapshot_id == first_code.snapshot_id
    assert observation.associated_code_snapshot_version == 1
    assert observation.associated_code_snapshot_id != second_code.snapshot_id


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
    assert completed.event_id is not None
    completed_observation = await ObservationEngine(db_session).project_event(completed.event_id)
    assert completed_observation.kind == "COUNTERQ_DELIVERY_COMPLETED"
    assert completed_observation.prompt_delivery_id == start.delivery_id
    assert completed_observation.transcript_segment_id == segment.id
    assert completed_observation.transcript_text == prompt.text

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
    assert interrupted.event_id is not None
    interrupted_observation = await ObservationEngine(db_session).project_event(
        interrupted.event_id,
    )
    assert interrupted_observation.kind == "COUNTERQ_DELIVERY_INTERRUPTED"
    assert interrupted_observation.prompt_delivery_id == second_start.delivery_id
    assert interrupted_observation.transcript_text is None

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

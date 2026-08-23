from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.interviews.dev_factory import create_development_interview
from app.interviews.floor import ConversationFloor
from app.interviews.runtime import IdempotencyConflict, InterviewRuntimeError
from app.realtime.control_protocol import (
    CandidateSpeechStartedMessage,
    CandidateSpeechStoppedMessage,
    CandidateTranscriptFinalizedMessage,
    ClientHelloMessage,
    ControlErrorMessage,
    ControlSignalAckMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
    DeliveryAckMessage,
    DevelopmentAuthorizedPromptMessage,
    DevelopmentAuthorizedPromptRequestMessage,
    DurableEventAckMessage,
    RealtimeDevelopmentBootstrapRequest,
    RealtimeDevelopmentBootstrapResponse,
    RealtimeDisconnectedMessage,
    RealtimeReconnectedMessage,
    ServerHelloMessage,
    client_control_message_adapter,
)
from app.realtime.control_service import RealtimeControlError, RealtimeControlService
from app.realtime.openai_provider import OpenAIRealtimeVoiceProvider
from app.realtime.provider import RealtimeProviderError, RealtimeVoiceProvider

router = APIRouter(prefix="/api/realtime", tags=["realtime"])
DEVELOPMENT_REALTIME_ENVS = frozenset({"local", "dev", "development", "test"})


class CreateRealtimeSessionRequest(BaseModel):
    purpose: Literal["interview_demo"] = "interview_demo"


class RealtimeTurnDetectionConfig(BaseModel):
    type: Literal["semantic_vad"]
    eagerness: Literal["low"]
    create_response: Literal[False]
    interrupt_response: Literal[True]


class CreateRealtimeSessionResponse(BaseModel):
    provider: Literal["openai"]
    client_secret: str = Field(description="Short-lived browser credential for OpenAI WebRTC.")
    webrtc_url: str
    model: str
    voice: str
    transcription_model: str
    expires_at: datetime | None
    expires_after_seconds: int
    turn_detection: RealtimeTurnDetectionConfig


def realtime_credential_minting_allowed(settings: Settings) -> bool:
    return settings.app_env.lower() in DEVELOPMENT_REALTIME_ENVS


def build_realtime_voice_provider(settings: Settings) -> RealtimeVoiceProvider:
    if settings.realtime_provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "category": "configuration_error",
                "message": "Configured realtime provider is unsupported",
            },
        )
    return OpenAIRealtimeVoiceProvider(settings)


def get_realtime_voice_provider_builder() -> Callable[[Settings], RealtimeVoiceProvider]:
    return build_realtime_voice_provider


@router.post("/session", response_model=CreateRealtimeSessionResponse)
async def create_realtime_session(
    _request: CreateRealtimeSessionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], RealtimeVoiceProvider],
        Depends(get_realtime_voice_provider_builder),
    ],
) -> CreateRealtimeSessionResponse:
    if not realtime_credential_minting_allowed(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Realtime credential minting is enabled only for local development",
            },
        )

    try:
        provider = provider_builder(settings)
        session = await provider.create_browser_session()
    except RealtimeProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc

    return CreateRealtimeSessionResponse(
        provider="openai",
        client_secret=session.client_secret,
        webrtc_url=session.webrtc_url,
        model=session.model,
        voice=session.voice,
        transcription_model=session.transcription_model,
        expires_at=session.expires_at,
        expires_after_seconds=session.expires_after_seconds,
        turn_detection=RealtimeTurnDetectionConfig(
            type="semantic_vad",
            eagerness="low",
            create_response=False,
            interrupt_response=True,
        ),
    )


@router.post(
    "/development-interview",
    response_model=RealtimeDevelopmentBootstrapResponse,
)
async def create_realtime_development_interview(
    _request: RealtimeDevelopmentBootstrapRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RealtimeDevelopmentBootstrapResponse:
    if not realtime_credential_minting_allowed(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Realtime development interview bootstrap is local-development only",
            },
        )

    async with session.begin():
        dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")

    interview = dev.interview_session
    return RealtimeDevelopmentBootstrapResponse(
        interview_session_id=interview.id,
        current_stage=interview.current_stage,
        state_version=interview.state_version,
        deadline_at=interview.deadline_at,
        control_websocket_path=f"/api/realtime/control/{interview.id}",
    )


@router.websocket("/control/{interview_session_id}")
async def realtime_control_websocket(
    websocket: WebSocket,
    interview_session_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not realtime_credential_minting_allowed(settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    sessionmaker = get_sessionmaker()
    floor = ConversationFloor()

    try:
        async with sessionmaker() as session:
            service = RealtimeControlService(session)
            interview = await service.ensure_session_exists(interview_session_id)
            await websocket.send_json(
                ServerHelloMessage(
                    interview_session_id=interview.id,
                    current_stage=interview.current_stage,
                    state_version=interview.state_version,
                    last_server_sequence=interview.last_server_sequence,
                ).model_dump(mode="json"),
            )
    except RealtimeControlError:
        await websocket.send_json(
            ControlErrorMessage(
                category="session_not_found",
                message="Interview session is not available for realtime control",
            ).model_dump(mode="json"),
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    while True:
        try:
            raw_message = await websocket.receive_json()
        except WebSocketDisconnect:
            return

        try:
            message = client_control_message_adapter.validate_python(raw_message)
        except ValidationError:
            await websocket.send_json(
                ControlErrorMessage(
                    category="invalid_message",
                    message="Realtime control message failed validation",
                ).model_dump(mode="json"),
            )
            continue

        try:
            if isinstance(message, ClientHelloMessage):
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateSpeechStartedMessage):
                floor = floor.candidate_speech_started()
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateSpeechStoppedMessage):
                floor = floor.candidate_paused()
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue

            async with sessionmaker() as session:
                async with session.begin():
                    service = RealtimeControlService(session)
                    service.floor = floor
                    response = await _handle_durable_control_message(
                        service=service,
                        interview_session_id=interview_session_id,
                        message=message,
                    )
                    floor = service.floor
                await websocket.send_json(response.model_dump(mode="json"))
        except (RealtimeControlError, InterviewRuntimeError) as exc:
            await websocket.send_json(
                ControlErrorMessage(
                    client_event_id=getattr(message, "client_event_id", None),
                    category="control_rejected",
                    message=safe_control_error_message(exc),
                ).model_dump(mode="json"),
            )


async def _handle_durable_control_message(
    *,
    service: RealtimeControlService,
    interview_session_id: UUID,
    message: object,
) -> (
    DurableEventAckMessage
    | DevelopmentAuthorizedPromptMessage
    | DeliveryAckMessage
    | ControlSignalAckMessage
):
    if isinstance(message, CandidateTranscriptFinalizedMessage):
        transcript_result = await service.persist_candidate_transcript(
            session_id=interview_session_id,
            message=message,
        )
        return DurableEventAckMessage(
            client_event_id=message.client_event_id,
            created=transcript_result.created,
            interview_event_id=transcript_result.event_id,
            transcript_segment_id=transcript_result.transcript_segment_id,
            server_sequence=transcript_result.server_sequence,
            interview_state_version=transcript_result.interview_state_version,
        )
    if isinstance(message, RealtimeDisconnectedMessage | RealtimeReconnectedMessage):
        connectivity_result = await service.persist_realtime_connectivity_event(
            session_id=interview_session_id,
            message=message,
        )
        return DurableEventAckMessage(
            client_event_id=message.client_event_id,
            created=connectivity_result.created,
            interview_event_id=connectivity_result.event_id,
            server_sequence=connectivity_result.server_sequence,
            interview_state_version=connectivity_result.interview_state_version,
        )
    if isinstance(message, DevelopmentAuthorizedPromptRequestMessage):
        prompt_result = await service.create_development_authorized_prompt(
            session_id=interview_session_id,
        )
        return DevelopmentAuthorizedPromptMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=prompt_result.prompt_id,
            text=prompt_result.text,
        )
    if isinstance(message, CounterQDeliveryStartedMessage):
        delivery_result = await service.start_delivery(
            session_id=interview_session_id,
            message=message,
        )
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
        )
    if isinstance(message, CounterQDeliveryCompletedMessage):
        delivery_result = await service.complete_delivery(
            session_id=interview_session_id,
            message=message,
        )
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            actual_transcript_segment_id=delivery_result.transcript_segment_id,
            interview_event_id=delivery_result.event_id,
            server_sequence=delivery_result.server_sequence,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
        )
    if isinstance(message, CounterQDeliveryInterruptedMessage):
        delivery_result = await service.interrupt_delivery(
            session_id=interview_session_id,
            message=message,
        )
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            interview_event_id=delivery_result.event_id,
            server_sequence=delivery_result.server_sequence,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
        )
    raise RealtimeControlError("Unsupported realtime control message")


def safe_control_error_message(exc: Exception) -> str:
    if isinstance(exc, IdempotencyConflict):
        return "Realtime control message conflicts with previously accepted truth"
    if isinstance(exc, RealtimeControlError):
        return "Realtime control message could not be accepted"
    return "Realtime control operation failed"

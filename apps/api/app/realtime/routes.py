from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import Settings, get_settings
from app.realtime.openai_provider import OpenAIRealtimeVoiceProvider
from app.realtime.provider import RealtimeProviderError, RealtimeVoiceProvider

router = APIRouter(prefix="/api/realtime", tags=["realtime"])


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


def get_realtime_voice_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RealtimeVoiceProvider:
    if settings.realtime_provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "category": "configuration_error",
                "message": "Configured realtime provider is unsupported",
            },
        )
    return OpenAIRealtimeVoiceProvider(settings)


@router.post("/session", response_model=CreateRealtimeSessionResponse)
async def create_realtime_session(
    _request: CreateRealtimeSessionRequest,
    provider: Annotated[RealtimeVoiceProvider, Depends(get_realtime_voice_provider)],
) -> CreateRealtimeSessionResponse:
    try:
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

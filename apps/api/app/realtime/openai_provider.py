from collections.abc import Mapping
from typing import Any, Protocol

import httpx
import structlog

from app.config.settings import Settings
from app.realtime.provider import (
    RealtimeBrowserSession,
    RealtimeConfigurationError,
    RealtimeMalformedResponseError,
    RealtimeUpstreamError,
    datetime_from_unix_seconds,
)

OPENAI_REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
OPENAI_REALTIME_WEBRTC_URL = "https://api.openai.com/v1/realtime/calls"

_logger = structlog.get_logger(__name__)


class AsyncPostClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        request_timeout: float,
    ) -> httpx.Response: ...


class OpenAIRealtimeVoiceProvider:
    provider_id = "openai"

    def __init__(self, settings: Settings, http_client: AsyncPostClient | None = None) -> None:
        self._settings = settings
        self._http_client = http_client

    async def create_browser_session(self) -> RealtimeBrowserSession:
        api_key = self._settings.openai_api_key
        if api_key is None:
            raise RealtimeConfigurationError(
                "OpenAI realtime is not configured for this local environment"
            )

        payload = self._build_client_secret_payload()
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "OpenAI-Safety-Identifier": "counterq-stage1-development",
        }

        try:
            if self._http_client is None:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                    OPENAI_REALTIME_CLIENT_SECRETS_URL,
                    headers=headers,
                    json=payload,
                    timeout=12.0,
                )
            else:
                response = await self._http_client.post(
                    OPENAI_REALTIME_CLIENT_SECRETS_URL,
                    headers=headers,
                    json=payload,
                    request_timeout=12.0,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _logger.warning(
                "realtime_provider_http_error",
                provider=self.provider_id,
                status_code=exc.response.status_code,
                model=self._settings.realtime_model,
            )
            raise RealtimeUpstreamError() from exc
        except httpx.HTTPError as exc:
            _logger.warning(
                "realtime_provider_transport_error",
                provider=self.provider_id,
                model=self._settings.realtime_model,
            )
            raise RealtimeUpstreamError() from exc

        return self._parse_client_secret_response(response.json())

    def _build_client_secret_payload(self) -> dict[str, Any]:
        return {
            "expires_after": {
                "anchor": "created_at",
                "seconds": self._settings.realtime_client_secret_ttl_seconds,
            },
            "session": {
                "type": "realtime",
                "model": self._settings.realtime_model,
                "output_modalities": ["audio"],
                "instructions": (
                    "You are CounterQ's realtime voice presence for a coding interview. "
                    "Stay calm, concise, and comfortable with silence. Do not judge correctness, "
                    "reveal solutions, choose technical probes, praise answers as correct, change "
                    "interview state, create evidence, or extend time. Speak only when CounterQ "
                    "software supplies candidate-safe authorized wording."
                ),
                "reasoning": {"effort": self._settings.realtime_reasoning_effort},
                "audio": {
                    "input": {
                        "transcription": {
                            "model": self._settings.realtime_transcription_model,
                        },
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "low",
                            "create_response": False,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "voice": self._settings.realtime_voice,
                    },
                },
            },
        }

    def _parse_client_secret_response(self, body: Mapping[str, Any]) -> RealtimeBrowserSession:
        secret_value: str | None = None
        expires_at: int | float | None = None

        client_secret = body.get("client_secret", body.get("value"))
        if isinstance(client_secret, str):
            secret_value = client_secret
        elif isinstance(client_secret, Mapping):
            raw_value = client_secret.get("value")
            if isinstance(raw_value, str):
                secret_value = raw_value
            raw_expires_at = client_secret.get("expires_at")
            if isinstance(raw_expires_at, int | float):
                expires_at = raw_expires_at

        raw_expires_at = body.get("expires_at")
        if expires_at is None and isinstance(raw_expires_at, int | float):
            expires_at = raw_expires_at

        if secret_value is None:
            raise RealtimeMalformedResponseError()

        return RealtimeBrowserSession(
            provider=self.provider_id,
            client_secret=secret_value,
            webrtc_url=OPENAI_REALTIME_WEBRTC_URL,
            model=self._settings.realtime_model,
            voice=self._settings.realtime_voice,
            transcription_model=self._settings.realtime_transcription_model,
            expires_at=datetime_from_unix_seconds(expires_at),
            expires_after_seconds=self._settings.realtime_client_secret_ttl_seconds,
        )

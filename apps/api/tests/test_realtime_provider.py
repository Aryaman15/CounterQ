from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings, create_settings, find_repository_root
from app.main import create_app
from app.realtime.openai_provider import (
    OPENAI_REALTIME_CLIENT_SECRETS_URL,
    OpenAIRealtimeVoiceProvider,
)
from app.realtime.provider import (
    RealtimeBrowserSession,
    RealtimeConfigurationError,
    RealtimeUpstreamError,
)
from app.realtime.routes import get_realtime_voice_provider


class RecordingPostClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.url: str | None = None
        self.headers: dict[str, str] | None = None
        self.json: dict[str, Any] | None = None

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        request_timeout: float,
    ) -> httpx.Response:
        self.url = url
        self.headers = dict(headers)
        self.json = dict(json)
        return self.response


class FakeRealtimeProvider:
    async def create_browser_session(self) -> RealtimeBrowserSession:
        return RealtimeBrowserSession(
            provider="openai",
            client_secret="ephemeral-test-secret",
            webrtc_url="https://api.openai.com/v1/realtime/calls",
            model="gpt-realtime-2.1",
            voice="marin",
            transcription_model="gpt-live-transcribe",
            expires_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            expires_after_seconds=600,
        )


def response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("POST", OPENAI_REALTIME_CLIENT_SECRETS_URL)
    return httpx.Response(status_code, json=body, request=request)


def settings_with_fake_openai_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=test-key\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return create_settings(env_file=env_file)


def test_repository_root_env_resolution_is_cwd_independent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    root = find_repository_root()

    assert (root / "AGENTS.md").is_file()
    assert (root / ".env").name == ".env"


def test_process_environment_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=from-env-file\nCOUNTERQ_REALTIME_MODEL=from-file\n")
    monkeypatch.setenv("OPENAI_API_KEY", "from-process")
    monkeypatch.setenv("COUNTERQ_REALTIME_MODEL", "from-process-model")

    settings = create_settings(env_file=env_file)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "from-process"
    assert settings.realtime_model == "from-process-model"


@pytest.mark.asyncio
async def test_missing_api_key_produces_normalized_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = create_settings(env_file=None)
    provider = OpenAIRealtimeVoiceProvider(settings)

    with pytest.raises(RealtimeConfigurationError) as exc_info:
        await provider.create_browser_session()

    assert exc_info.value.category == "configuration_error"
    assert "key" not in exc_info.value.safe_message.lower()


@pytest.mark.asyncio
async def test_openai_adapter_targets_client_secret_endpoint_and_configures_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = settings_with_fake_openai_key(tmp_path, monkeypatch)
    client = RecordingPostClient(
        response(
            200,
            {
                "client_secret": {"value": "ephemeral", "expires_at": 1787572800},
            },
        )
    )
    provider = OpenAIRealtimeVoiceProvider(settings, http_client=client)

    browser_session = await provider.create_browser_session()

    assert client.url == OPENAI_REALTIME_CLIENT_SECRETS_URL
    assert browser_session.client_secret == "ephemeral"
    assert browser_session.model == "gpt-realtime-2.1"
    assert browser_session.voice == "marin"
    assert client.json is not None
    session_config = client.json["session"]
    assert session_config["model"] == "gpt-realtime-2.1"
    assert session_config["reasoning"] == {"effort": "low"}
    assert session_config["audio"]["output"]["voice"] == "marin"
    assert session_config["audio"]["input"]["transcription"]["model"] == "gpt-live-transcribe"
    turn_detection = session_config["audio"]["input"]["turn_detection"]
    assert turn_detection == {
        "type": "semantic_vad",
        "eagerness": "low",
        "create_response": False,
        "interrupt_response": True,
    }


@pytest.mark.asyncio
async def test_openai_provider_errors_normalize_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = settings_with_fake_openai_key(tmp_path, monkeypatch)
    provider = OpenAIRealtimeVoiceProvider(
        settings,
        http_client=RecordingPostClient(response(429, {"error": {"message": "quota"}})),
    )

    with pytest.raises(RealtimeUpstreamError) as exc_info:
        await provider.create_browser_session()

    assert exc_info.value.category == "upstream_error"
    assert "test-key" not in exc_info.value.safe_message


@pytest.mark.asyncio
async def test_realtime_endpoint_response_contains_only_candidate_safe_fields() -> None:
    app = create_app()
    app.dependency_overrides[get_realtime_voice_provider] = lambda: FakeRealtimeProvider()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        result = await client.post("/api/realtime/session", json={"purpose": "interview_demo"})

    body = result.json()
    assert result.status_code == 200
    assert body["client_secret"] == "ephemeral-test-secret"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-realtime-2.1"
    assert body["voice"] == "marin"
    assert body["turn_detection"]["create_response"] is False
    assert "instructions" not in body
    assert "Authorization" not in str(body)

    app.dependency_overrides.clear()

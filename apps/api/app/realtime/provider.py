from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class RealtimeBrowserSession:
    provider: str
    client_secret: str
    webrtc_url: str
    model: str
    voice: str
    transcription_model: str
    expires_at: datetime | None
    expires_after_seconds: int


class RealtimeVoiceProvider(Protocol):
    async def create_browser_session(self) -> RealtimeBrowserSession:
        """Create short-lived browser credentials for a realtime voice session."""


class RealtimeProviderError(RuntimeError):
    def __init__(self, category: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message


class RealtimeConfigurationError(RealtimeProviderError):
    def __init__(self, safe_message: str) -> None:
        super().__init__("configuration_error", safe_message)


class RealtimeUpstreamError(RealtimeProviderError):
    def __init__(self, safe_message: str = "Realtime provider request failed") -> None:
        super().__init__("upstream_error", safe_message)


class RealtimeMalformedResponseError(RealtimeProviderError):
    def __init__(self) -> None:
        super().__init__("malformed_response", "Realtime provider returned an invalid response")


def datetime_from_unix_seconds(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)

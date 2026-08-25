from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repository_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / "AGENTS.md").is_file() and (candidate / "package.json").is_file():
            return candidate
    raise RuntimeError("Unable to locate CounterQ repository root")


REPOSITORY_ROOT = find_repository_root()
REPOSITORY_ENV_FILE = REPOSITORY_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COUNTERQ_",
        extra="ignore",
    )

    app_name: str = "counterq-api"
    app_env: str = "local"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://counterq:counterq@localhost:5432/counterq",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="REDIS_URL",
    )
    local_web_origin: str = Field(
        default="http://127.0.0.1:3000",
        validation_alias="COUNTERQ_LOCAL_WEB_ORIGIN",
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    realtime_provider: str = Field(
        default="openai",
        validation_alias="COUNTERQ_REALTIME_PROVIDER",
    )
    realtime_model: str = Field(
        default="gpt-realtime-2.1",
        validation_alias="COUNTERQ_REALTIME_MODEL",
    )
    realtime_voice: str = Field(
        default="marin",
        validation_alias="COUNTERQ_REALTIME_VOICE",
    )
    realtime_transcription_model: str = Field(
        default="gpt-live-transcribe",
        validation_alias="COUNTERQ_REALTIME_TRANSCRIPTION_MODEL",
    )
    realtime_reasoning_effort: str = Field(
        default="low",
        validation_alias="COUNTERQ_REALTIME_REASONING_EFFORT",
    )
    realtime_client_secret_ttl_seconds: int = Field(
        default=600,
        validation_alias="COUNTERQ_REALTIME_CLIENT_SECRET_TTL_SECONDS",
    )

    reasoning_provider: str = Field(
        default="openai",
        validation_alias="COUNTERQ_REASONING_PROVIDER",
    )
    reasoning_standard_model: str = Field(
        default="gpt-5.6-terra",
        validation_alias="COUNTERQ_REASONING_STANDARD_MODEL",
    )
    reasoning_strong_model: str = Field(
        default="gpt-5.6-sol",
        validation_alias="COUNTERQ_REASONING_STRONG_MODEL",
    )
    reasoning_standard_effort: str = Field(
        default="medium",
        validation_alias="COUNTERQ_REASONING_STANDARD_EFFORT",
    )
    reasoning_strong_effort: str = Field(
        default="high",
        validation_alias="COUNTERQ_REASONING_STRONG_EFFORT",
    )
    reasoning_timeout_seconds: float = Field(
        default=20.0,
        validation_alias="COUNTERQ_REASONING_TIMEOUT_SECONDS",
    )
    live_examiner_autostart: bool = Field(
        default=False,
        validation_alias="COUNTERQ_LIVE_EXAMINER_AUTOSTART",
    )
    live_examiner_usefulness_seconds: float = Field(
        default=8.0,
        validation_alias="COUNTERQ_LIVE_EXAMINER_USEFULNESS_SECONDS",
    )
    authorized_prompt_delivery_window_seconds: float = Field(
        default=12.0,
        validation_alias="COUNTERQ_AUTHORIZED_PROMPT_DELIVERY_WINDOW_SECONDS",
    )
    execution_provider: str = Field(
        default="local_sandbox",
        validation_alias="COUNTERQ_EXECUTION_PROVIDER",
    )
    execution_sandbox_url: str = Field(
        default="http://127.0.0.1:8010",
        validation_alias="COUNTERQ_EXECUTION_SANDBOX_URL",
    )
    execution_compile_timeout_seconds: int = Field(default=8, ge=1, le=20)
    execution_run_timeout_seconds: int = Field(default=2, ge=1, le=10)
    execution_memory_limit_mb: int = Field(default=192, ge=64, le=512)
    execution_output_limit_bytes: int = Field(default=65536, ge=1024, le=131072)


def create_settings(env_file: Path | str | None = REPOSITORY_ENV_FILE) -> Settings:
    return Settings(_env_file=env_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return create_settings()

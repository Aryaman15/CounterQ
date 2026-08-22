from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


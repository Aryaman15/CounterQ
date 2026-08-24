from __future__ import annotations

from app.config.settings import Settings

DEVELOPMENT_SPIKE_ENVS = frozenset({"local", "dev", "development", "test"})


def development_spike_enabled(settings: Settings) -> bool:
    return settings.app_env.lower() in DEVELOPMENT_SPIKE_ENVS

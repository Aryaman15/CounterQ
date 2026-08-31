from __future__ import annotations

from app.ai_gateway.provider import ReasoningProvider
from app.ai_gateway.providers.openai_reasoning import OpenAIReasoningProvider
from app.config.settings import Settings


class ReasoningProviderConfigurationError(ValueError):
    pass


def build_reasoning_provider(settings: Settings) -> ReasoningProvider:
    """Construct the configured provider behind the shared AI Gateway boundary."""
    if settings.reasoning_provider != "openai":
        raise ReasoningProviderConfigurationError("Configured reasoning provider is unsupported")
    return OpenAIReasoningProvider(settings)

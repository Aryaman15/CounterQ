from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

ReasoningCapability = Literal["STANDARD_REASONING", "STRONG_REASONING"]
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
ReasoningErrorCategory = Literal[
    "AUTHENTICATION",
    "RATE_LIMIT",
    "INVALID_REQUEST",
    "TIMEOUT",
    "TRANSIENT_PROVIDER",
    "PROVIDER_UNAVAILABLE",
    "STRUCTURED_OUTPUT_INVALID",
    "CANCELLED",
    "UNKNOWN_PROVIDER_ERROR",
    "CONFIGURATION_ERROR",
    "BUDGET_EXHAUSTED",
    "POLICY_CONFLICT",
]


@dataclass(frozen=True)
class ReasoningPolicyDescriptor:
    policy_key: str
    version: str
    instructions: str
    configuration: dict[str, object] = field(default_factory=dict)
    code_revision: str | None = None


@dataclass(frozen=True)
class ReasoningRequest:
    capability: ReasoningCapability
    purpose: str
    policy: ReasoningPolicyDescriptor
    instructions: str
    input_content: str
    output_schema_name: str
    output_json_schema: dict[str, Any]
    timeout_seconds: float
    usefulness_deadline: datetime | None = None
    user_id: UUID | None = None
    interview_session_id: UUID | None = None
    correlation_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasoningUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderReasoningResult:
    output_data: dict[str, Any]
    provider: str
    model: str
    provider_model_version: str | None
    provider_request_id: str | None
    usage: ReasoningUsage
    latency_ms: int
    retry_count: int
    estimated_cost: Decimal | None = None
    currency: str | None = None


class ReasoningProviderError(Exception):
    def __init__(
        self,
        category: ReasoningErrorCategory,
        safe_message: str = "Reasoning provider request failed",
        *,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
        provider_error_param: str | None = None,
        safe_provider_message: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.provider_error_type = provider_error_type
        self.provider_error_code = provider_error_code
        self.provider_error_param = provider_error_param
        self.safe_provider_message = safe_provider_message


class ReasoningProvider(Protocol):
    provider_name: str

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        """Execute one schema-constrained reasoning request."""

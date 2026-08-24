from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai_gateway.gateway import (
    AIGateway,
    AIGatewayError,
    ReasoningBudgetExceeded,
    StructuredOutputSchemaInvalid,
    StructuredOutputValidationFailure,
)
from app.ai_gateway.provider import (
    ReasoningPolicyDescriptor,
    ReasoningProvider,
    ReasoningProviderError,
)
from app.ai_gateway.providers.openai_reasoning import OpenAIReasoningProvider
from app.ai_gateway.structured_output import StrictReasoningOutputModel
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_sessionmaker

router = APIRouter(prefix="/api/ai", tags=["ai-gateway"])

SMOKE_POLICY_KEY = "development_reasoning_smoke"
SMOKE_POLICY_VERSION = "v1"
SMOKE_INSTRUCTIONS = (
    "You are a CounterQ development smoke-test reasoner. Return only the requested "
    "candidate-safe structured fields. Do not provide chain-of-thought. Evaluate the "
    "specific technical claim factually and concisely."
)
SMOKE_INPUT = "In C++, unordered_map lookup is always guaranteed O(1)."


class DevelopmentReasoningSmokeRequest(BaseModel):
    interview_session_id: UUID


class DevelopmentReasoningSmokeResult(StrictReasoningOutputModel):
    verdict: Literal["GUARANTEED", "NOT_GUARANTEED", "UNCERTAIN"]
    technical_note: str = Field(max_length=280)
    confidence: float = Field(ge=0, le=1)


class DevelopmentReasoningSmokeResponse(BaseModel):
    invocation_id: UUID
    status: Literal["SUCCEEDED"]
    provider: str
    model: str
    capability: Literal["STANDARD_REASONING"]
    verdict: Literal["GUARANTEED", "NOT_GUARANTEED", "UNCERTAIN"]
    technical_note: str
    confidence: float
    latency_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal | None
    currency: str | None
    reasoning_budget_used: int
    reasoning_budget_remaining: int


def build_reasoning_provider(settings: Settings) -> ReasoningProvider:
    if settings.reasoning_provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "category": "configuration_error",
                "message": "Configured reasoning provider is unsupported",
            },
        )
    return OpenAIReasoningProvider(settings)


def get_reasoning_provider_builder() -> Callable[[Settings], ReasoningProvider]:
    return build_reasoning_provider


@router.post(
    "/development-reasoning-smoke",
    response_model=DevelopmentReasoningSmokeResponse,
)
async def development_reasoning_smoke(
    request: DevelopmentReasoningSmokeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], ReasoningProvider],
        Depends(get_reasoning_provider_builder),
    ],
) -> DevelopmentReasoningSmokeResponse:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "AI Gateway smoke testing is enabled only for local development",
            },
        )

    provider = provider_builder(settings)
    gateway = AIGateway(
        settings=settings,
        sessionmaker=get_sessionmaker(),
        provider=provider,
    )
    try:
        result = await gateway.reason_structured(
            interview_session_id=request.interview_session_id,
            capability="STANDARD_REASONING",
            purpose="development_reasoning_smoke",
            policy=ReasoningPolicyDescriptor(
                policy_key=SMOKE_POLICY_KEY,
                version=SMOKE_POLICY_VERSION,
                instructions=SMOKE_INSTRUCTIONS,
                configuration={
                    "schema": DevelopmentReasoningSmokeResult.__name__,
                    "capability": "STANDARD_REASONING",
                },
            ),
            instructions=SMOKE_INSTRUCTIONS,
            input_content=SMOKE_INPUT,
            output_model=DevelopmentReasoningSmokeResult,
        )
    except ReasoningBudgetExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except StructuredOutputValidationFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except StructuredOutputSchemaInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except ReasoningProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except AIGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc

    parsed = result.parsed
    return DevelopmentReasoningSmokeResponse(
        invocation_id=result.invocation_id,
        status="SUCCEEDED",
        provider=result.provider,
        model=result.model,
        capability="STANDARD_REASONING",
        verdict=parsed.verdict,
        technical_note=parsed.technical_note,
        confidence=parsed.confidence,
        latency_ms=result.latency_ms,
        input_tokens=result.usage.input_tokens,
        cached_input_tokens=result.usage.cached_input_tokens,
        output_tokens=result.usage.output_tokens,
        estimated_cost=result.estimated_cost,
        currency=result.currency,
        reasoning_budget_used=result.budget_used,
        reasoning_budget_remaining=result.budget_remaining,
    )

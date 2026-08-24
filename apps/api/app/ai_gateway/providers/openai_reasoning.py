from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
import structlog

from app.ai_gateway.pricing import estimate_text_token_cost
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningErrorCategory,
    ReasoningProvider,
    ReasoningProviderError,
    ReasoningRequest,
    ReasoningUsage,
)
from app.config.settings import Settings

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
logger = structlog.get_logger(__name__)


class AsyncPostClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        request_timeout: float,
    ) -> httpx.Response:
        """Post a JSON request."""


class OpenAIReasoningProvider(ReasoningProvider):
    provider_name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: AsyncPostClient | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        if self._settings.openai_api_key is None:
            raise ReasoningProviderError(
                "CONFIGURATION_ERROR",
                "Reasoning provider is not configured",
            )

        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": model,
            "instructions": request.instructions,
            "input": request.input_content,
            "reasoning": {"effort": reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_json_schema,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

        try:
            if self._http_client is not None:
                response = await self._http_client.post(
                    OPENAI_RESPONSES_URL,
                    headers=headers,
                    json=payload,
                    request_timeout=request.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        OPENAI_RESPONSES_URL,
                        headers=headers,
                        json=payload,
                        timeout=request.timeout_seconds,
                    )
        except httpx.TimeoutException as exc:
            raise ReasoningProviderError("TIMEOUT", "Reasoning provider timed out") from exc
        except httpx.HTTPError as exc:
            raise ReasoningProviderError(
                "TRANSIENT_PROVIDER",
                "Reasoning provider transport failed",
            ) from exc

        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        provider_request_id = response.headers.get("request-id") or response.headers.get(
            "x-request-id"
        )

        if response.status_code >= 400:
            error_details = _extract_safe_error_details(response)
            if self._settings.app_env.lower() in {"local", "dev", "development", "test"}:
                logger.warning(
                    "openai_reasoning_provider_rejected_request",
                    provider="openai",
                    status_code=response.status_code,
                    category=_category_for_status(response.status_code),
                    provider_error_type=error_details.provider_error_type,
                    provider_error_code=error_details.provider_error_code,
                    provider_error_param=error_details.provider_error_param,
                    safe_provider_message=error_details.safe_provider_message,
                )
            raise ReasoningProviderError(
                _category_for_status(response.status_code),
                "Reasoning provider rejected the request",
                provider_error_type=error_details.provider_error_type,
                provider_error_code=error_details.provider_error_code,
                provider_error_param=error_details.provider_error_param,
                safe_provider_message=error_details.safe_provider_message,
            )

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ReasoningProviderError(
                "STRUCTURED_OUTPUT_INVALID",
                "Reasoning provider returned malformed structured output",
            ) from exc

        output_text = _extract_output_text(body)
        if output_text is None:
            raise ReasoningProviderError(
                "STRUCTURED_OUTPUT_INVALID",
                "Reasoning provider response did not contain structured output",
            )

        try:
            output_data = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise ReasoningProviderError(
                "STRUCTURED_OUTPUT_INVALID",
                "Reasoning provider returned malformed structured output",
            ) from exc
        if not isinstance(output_data, dict):
            raise ReasoningProviderError(
                "STRUCTURED_OUTPUT_INVALID",
                "Reasoning provider returned an unexpected structured output shape",
            )

        usage = _extract_usage(body)
        estimated = estimate_text_token_cost(model, usage)
        cost = estimated[0] if estimated is not None else None
        currency = estimated[1] if estimated is not None else None

        return ProviderReasoningResult(
            output_data=output_data,
            provider="openai",
            model=str(body.get("model") or model),
            provider_model_version=str(body.get("model")) if body.get("model") else None,
            provider_request_id=str(body.get("id") or provider_request_id)
            if body.get("id") or provider_request_id
            else None,
            usage=usage,
            latency_ms=latency_ms,
            retry_count=0,
            estimated_cost=cost,
            currency=currency,
        )


def _category_for_status(status_code: int) -> ReasoningErrorCategory:
    if status_code in {401, 403}:
        return "AUTHENTICATION"
    if status_code == 429:
        return "RATE_LIMIT"
    if status_code == 408:
        return "TIMEOUT"
    if 400 <= status_code < 500:
        return "INVALID_REQUEST"
    if status_code >= 500:
        return "PROVIDER_UNAVAILABLE"
    return "UNKNOWN_PROVIDER_ERROR"


class SafeProviderErrorDetails:
    def __init__(
        self,
        *,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
        provider_error_param: str | None = None,
        safe_provider_message: str | None = None,
    ) -> None:
        self.provider_error_type = provider_error_type
        self.provider_error_code = provider_error_code
        self.provider_error_param = provider_error_param
        self.safe_provider_message = safe_provider_message


def _extract_safe_error_details(response: httpx.Response) -> SafeProviderErrorDetails:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return SafeProviderErrorDetails()
    if not isinstance(body, Mapping):
        return SafeProviderErrorDetails()
    error = body.get("error")
    if not isinstance(error, Mapping):
        return SafeProviderErrorDetails()
    return SafeProviderErrorDetails(
        provider_error_type=_safe_str(error.get("type")),
        provider_error_code=_safe_str(error.get("code")),
        provider_error_param=_safe_str(error.get("param")),
        safe_provider_message=_safe_str(error.get("message")),
    )


def _safe_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _extract_output_text(body: Mapping[str, Any]) -> str | None:
    direct = body.get("output_text")
    if isinstance(direct, str):
        return direct

    fragments: list[str] = []
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "output_text":
                    text = block.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
    if fragments:
        return "".join(fragments)
    return None


def _extract_usage(body: Mapping[str, Any]) -> ReasoningUsage:
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return ReasoningUsage()

    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    cached_input_tokens = None
    details = usage.get("input_tokens_details")
    if isinstance(details, Mapping):
        cached_input_tokens = _int_or_none(details.get("cached_tokens"))

    return ReasoningUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None

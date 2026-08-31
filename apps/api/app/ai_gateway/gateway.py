from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningCapability,
    ReasoningEffort,
    ReasoningPolicyDescriptor,
    ReasoningProvider,
    ReasoningProviderError,
    ReasoningRequest,
    ReasoningUsage,
)
from app.ai_gateway.structured_output import (
    StructuredOutputSchemaError,
    validate_strict_reasoning_schema,
)
from app.config.settings import Settings
from app.interviews.models import InterviewSession, SessionBudget

T = TypeVar("T", bound=BaseModel)


class AIGatewayError(Exception):
    category = "AI_GATEWAY_ERROR"

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class PolicyVersionConflict(AIGatewayError):
    category = "POLICY_CONFLICT"


class ReasoningBudgetExceeded(AIGatewayError):
    category = "BUDGET_EXHAUSTED"


class ReasoningSessionNotFound(AIGatewayError):
    category = "SESSION_NOT_FOUND"


class StructuredOutputValidationFailure(AIGatewayError):
    category = "STRUCTURED_OUTPUT_INVALID"


class StructuredOutputSchemaInvalid(AIGatewayError):
    category = "STRUCTURED_OUTPUT_SCHEMA_INVALID"


@dataclass(frozen=True)
class AIGatewayResult[T: BaseModel]:
    invocation_id: UUID
    provider: str
    model: str
    capability: ReasoningCapability
    policy_version_id: UUID
    parsed: T
    usage: ReasoningUsage
    latency_ms: int | None
    retry_count: int
    estimated_cost: Decimal | None
    currency: str | None
    budget_used: int
    budget_remaining: int


@dataclass(frozen=True)
class PreparedInvocation:
    invocation_id: UUID
    policy_version_id: UUID
    user_id: UUID
    budget_used: int
    budget_remaining: int


class AIGateway:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: ReasoningProvider,
        transaction_probe: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._provider = provider
        self._transaction_probe = transaction_probe
        self._active_transaction_count = 0

    @property
    def active_transaction_count(self) -> int:
        return self._active_transaction_count

    async def reason_structured(
        self,
        *,
        interview_session_id: UUID,
        capability: ReasoningCapability,
        purpose: str,
        policy: ReasoningPolicyDescriptor,
        instructions: str,
        input_content: str,
        output_model: type[T],
        timeout_seconds: float | None = None,
        usefulness_deadline: datetime | None = None,
        reasoning_effort_override: ReasoningEffort | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AIGatewayResult[T]:
        model = self.model_for_capability(capability)
        effort = (
            self.validate_reasoning_effort(reasoning_effort_override)
            if reasoning_effort_override is not None
            else self.effort_for_capability(capability)
        )
        request_timeout = timeout_seconds or self._settings.reasoning_timeout_seconds

        schema = output_model.model_json_schema()
        try:
            validate_strict_reasoning_schema(schema)
        except StructuredOutputSchemaError as exc:
            raise StructuredOutputSchemaInvalid(str(exc)) from exc

        request = ReasoningRequest(
            capability=capability,
            purpose=purpose,
            policy=policy,
            instructions=instructions,
            input_content=input_content,
            output_schema_name=output_model.__name__,
            output_json_schema=schema,
            timeout_seconds=request_timeout,
            usefulness_deadline=usefulness_deadline,
            interview_session_id=interview_session_id,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )

        prepared = await self._prepare_invocation(
            interview_session_id=interview_session_id,
            provider_name=self._provider.provider_name,
            model=model,
            capability=capability,
            purpose=purpose,
            policy=policy,
        )
        request = ReasoningRequest(
            **{
                **request.__dict__,
                "user_id": prepared.user_id,
            }
        )

        if self._transaction_probe is not None and self._transaction_probe():
            raise RuntimeError("AI provider call attempted while database transaction is open")

        try:
            provider_result = await asyncio.wait_for(
                self._provider.reason_structured(
                    request,
                    model=model,
                    reasoning_effort=effort,
                ),
                timeout=request_timeout,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_failed_invocation(
                    prepared.invocation_id,
                    status="CANCELLED",
                    error_class="CANCELLED",
                )
            )
            raise
        except TimeoutError:
            await self._finish_failed_invocation(
                prepared.invocation_id,
                status="TIMED_OUT",
                error_class="TIMEOUT",
            )
            raise ReasoningProviderError("TIMEOUT", "Reasoning request timed out") from None
        except ReasoningProviderError as exc:
            status = "TIMED_OUT" if exc.category == "TIMEOUT" else "FAILED"
            await self._finish_failed_invocation(
                prepared.invocation_id,
                status=status,
                error_class=exc.category,
            )
            raise

        try:
            parsed = output_model.model_validate(provider_result.output_data)
        except ValidationError as exc:
            await self._finish_failed_invocation(
                prepared.invocation_id,
                status="FAILED",
                error_class="STRUCTURED_OUTPUT_INVALID",
            )
            raise StructuredOutputValidationFailure(
                "Reasoning provider output failed schema validation"
            ) from exc

        await self._finish_successful_invocation(prepared.invocation_id, provider_result)

        return AIGatewayResult(
            invocation_id=prepared.invocation_id,
            provider=provider_result.provider,
            model=provider_result.model,
            capability=capability,
            policy_version_id=prepared.policy_version_id,
            parsed=parsed,
            usage=provider_result.usage,
            latency_ms=provider_result.latency_ms,
            retry_count=provider_result.retry_count,
            estimated_cost=provider_result.estimated_cost,
            currency=provider_result.currency,
            budget_used=prepared.budget_used,
            budget_remaining=prepared.budget_remaining,
        )

    def model_for_capability(self, capability: ReasoningCapability) -> str:
        if capability == "STANDARD_REASONING":
            return self._settings.reasoning_standard_model
        return self._settings.reasoning_strong_model

    def effort_for_capability(self, capability: ReasoningCapability) -> ReasoningEffort:
        effort = (
            self._settings.reasoning_standard_effort
            if capability == "STANDARD_REASONING"
            else self._settings.reasoning_strong_effort
        )
        return self.validate_reasoning_effort(effort)

    def validate_reasoning_effort(self, effort: str) -> ReasoningEffort:
        if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise AIGatewayError("Configured reasoning effort is unsupported")
        return cast(ReasoningEffort, effort)

    async def _prepare_invocation(
        self,
        *,
        interview_session_id: UUID,
        provider_name: str,
        model: str,
        capability: ReasoningCapability,
        purpose: str,
        policy: ReasoningPolicyDescriptor,
    ) -> PreparedInvocation:
        async with self._sessionmaker() as session:
            self._active_transaction_count += 1
            try:
                async with session.begin():
                    interview = await session.get(InterviewSession, interview_session_id)
                    if interview is None:
                        raise ReasoningSessionNotFound("Interview session was not found")
                    policy_version = await get_or_create_policy_version(session, policy)
                    budget = await _lock_budget(session, interview_session_id)
                    budget_used, budget_remaining = _reserve_reasoning_budget(budget, capability)
                    invocation = AIInvocation(
                        user_id=interview.user_id,
                        interview_session_id=interview.id,
                        provider=provider_name,
                        model=model,
                        capability=capability,
                        purpose=purpose,
                        ai_policy_version_id=policy_version.id,
                        status="STARTED",
                        started_at=datetime.now(UTC),
                        retry_count=0,
                    )
                    session.add(invocation)
                    await session.flush()
                    return PreparedInvocation(
                        invocation_id=invocation.id,
                        policy_version_id=policy_version.id,
                        user_id=interview.user_id,
                        budget_used=budget_used,
                        budget_remaining=budget_remaining,
                    )
            finally:
                self._active_transaction_count -= 1

    async def _finish_successful_invocation(
        self,
        invocation_id: UUID,
        provider_result: ProviderReasoningResult,
    ) -> None:
        async with self._sessionmaker() as session:
            self._active_transaction_count += 1
            try:
                async with session.begin():
                    invocation = await session.get(AIInvocation, invocation_id)
                    if invocation is None:
                        raise AIGatewayError("AI invocation disappeared before completion")
                    invocation.status = "SUCCEEDED"
                    invocation.completed_at = datetime.now(UTC)
                    invocation.latency_ms = provider_result.latency_ms
                    invocation.provider_request_id = provider_result.provider_request_id
                    invocation.provider_model_version = provider_result.provider_model_version
                    invocation.input_tokens = provider_result.usage.input_tokens
                    invocation.cached_input_tokens = provider_result.usage.cached_input_tokens
                    invocation.output_tokens = provider_result.usage.output_tokens
                    invocation.retry_count = provider_result.retry_count
                    invocation.estimated_cost = provider_result.estimated_cost
                    invocation.currency = provider_result.currency
                    if (
                        provider_result.estimated_cost is not None
                        and invocation.interview_session_id
                    ):
                        budget = await _lock_budget(session, invocation.interview_session_id)
                        budget.estimated_cost += provider_result.estimated_cost
            finally:
                self._active_transaction_count -= 1

    async def _finish_failed_invocation(
        self,
        invocation_id: UUID,
        *,
        status: str,
        error_class: str,
    ) -> None:
        async with self._sessionmaker() as session:
            self._active_transaction_count += 1
            try:
                async with session.begin():
                    invocation = await session.get(AIInvocation, invocation_id)
                    if invocation is None:
                        raise AIGatewayError("AI invocation disappeared before failure recording")
                    invocation.status = status
                    invocation.completed_at = datetime.now(UTC)
                    invocation.error_class = error_class
            finally:
                self._active_transaction_count -= 1


async def get_or_create_policy_version(
    session: AsyncSession,
    policy: ReasoningPolicyDescriptor,
) -> AIPolicyVersion:
    prompt_hash = policy_instruction_hash(policy.instructions)
    existing = await session.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.policy_key == policy.policy_key,
            AIPolicyVersion.version == policy.version,
        )
    )
    if existing is not None:
        if (
            existing.prompt_hash != prompt_hash
            or existing.configuration_json != canonical_json_object(policy.configuration)
            or existing.code_revision != policy.code_revision
        ):
            raise PolicyVersionConflict(
                "AI policy key/version already exists with different immutable semantics"
            )
        return existing

    policy_version = AIPolicyVersion(
        policy_key=policy.policy_key,
        version=policy.version,
        prompt_hash=prompt_hash,
        configuration_json=canonical_json_object(policy.configuration),
        code_revision=policy.code_revision,
        activated_at=datetime.now(UTC),
    )
    session.add(policy_version)
    await session.flush()
    return policy_version


def policy_instruction_hash(instructions: str) -> str:
    return "sha256:" + hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def canonical_json_object(value: dict[str, object]) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"))),
    )


async def _lock_budget(session: AsyncSession, interview_session_id: UUID) -> SessionBudget:
    budget = await session.scalar(
        select(SessionBudget)
        .where(SessionBudget.session_id == interview_session_id)
        .with_for_update()
    )
    if budget is None:
        raise ReasoningBudgetExceeded("Interview session does not have a reasoning budget")
    return budget


def _reserve_reasoning_budget(
    budget: SessionBudget,
    capability: ReasoningCapability,
) -> tuple[int, int]:
    if budget.estimated_cost >= budget.hard_monetary_budget:
        raise ReasoningBudgetExceeded("Interview session has reached its hard reasoning budget")

    if capability == "STANDARD_REASONING":
        if budget.deep_reasoning_used >= budget.max_deep_reasoning_calls:
            raise ReasoningBudgetExceeded("Deep reasoning budget is exhausted")
        budget.deep_reasoning_used += 1
        return (
            budget.deep_reasoning_used,
            budget.max_deep_reasoning_calls - budget.deep_reasoning_used,
        )

    if budget.strong_reasoning_used >= budget.max_strong_reasoning_calls:
        raise ReasoningBudgetExceeded("Strong reasoning budget is exhausted")
    budget.strong_reasoning_used += 1
    return (
        budget.strong_reasoning_used,
        budget.max_strong_reasoning_calls - budget.strong_reasoning_used,
    )

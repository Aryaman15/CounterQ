from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import (
    POST_INTERVIEW_ASSESSMENT_PURPOSE,
    AIGateway,
    PolicyVersionConflict,
    ReasoningBudgetExceeded,
    StructuredOutputSchemaInvalid,
    StructuredOutputValidationFailure,
    get_or_create_policy_version,
    policy_instruction_hash,
)
from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.ai_gateway.pricing import estimate_text_token_cost
from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningPolicyDescriptor,
    ReasoningProviderError,
    ReasoningRequest,
    ReasoningUsage,
)
from app.ai_gateway.providers.openai_reasoning import OPENAI_RESPONSES_URL, OpenAIReasoningProvider
from app.ai_gateway.routes import get_reasoning_provider_builder
from app.ai_gateway.structured_output import (
    StrictReasoningOutputModel,
    StructuredOutputSchemaError,
    validate_strict_reasoning_schema,
)
from app.config.settings import Settings, create_settings, get_settings
from app.db.session import build_engine, dispose_engine, get_session
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.policy import LIVE_EXAMINER_INSTRUCTIONS, live_examiner_policy_descriptor
from app.interviews.budget_policy import budget_availability, interactive_deep_reasoning_limit
from app.interviews.dev_factory import DevelopmentInterview, create_development_interview
from app.interviews.models import InterviewerPrompt, SessionBudget
from app.main import create_app
from app.realtime.control_protocol import RealtimeDevelopmentBootstrapRequest


class SmokeResult(StrictReasoningOutputModel):
    verdict: str
    technical_note: str
    confidence: float = Field(ge=0, le=1)


class StrictNestedDetail(StrictReasoningOutputModel):
    label: str


class StrictNestedResult(StrictReasoningOutputModel):
    verdict: str
    detail: StrictNestedDetail


class PermissiveResult(BaseModel):
    verdict: str


class MissingRequiredResult(StrictReasoningOutputModel):
    verdict: str
    optional_note: str | None = None


class PermissiveNestedDetail(BaseModel):
    label: str


class IncompatibleNestedResult(StrictReasoningOutputModel):
    verdict: str
    detail: PermissiveNestedDetail


class FakeReasoningProvider:
    provider_name = "fake"

    def __init__(
        self,
        *,
        output_data: dict[str, Any] | None = None,
        error: ReasoningProviderError | None = None,
        delay_seconds: float = 0,
        assert_no_gateway_transaction: AIGateway | None = None,
    ) -> None:
        self.output_data = output_data or {
            "verdict": "NOT_GUARANTEED",
            "technical_note": (
                "Average lookup is expected constant time; worst-case is not guaranteed."
            ),
            "confidence": 0.91,
        }
        self.error = error
        self.delay_seconds = delay_seconds
        self.assert_no_gateway_transaction = assert_no_gateway_transaction
        self.calls = 0
        self.requests: list[ReasoningRequest] = []
        self.models: list[str] = []
        self.efforts: list[str] = []
        self.called_event = asyncio.Event()

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        self.calls += 1
        self.called_event.set()
        self.requests.append(request)
        self.models.append(model)
        self.efforts.append(reasoning_effort)
        if self.assert_no_gateway_transaction is not None:
            assert self.assert_no_gateway_transaction.active_transaction_count == 0
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return ProviderReasoningResult(
            output_data=self.output_data,
            provider="fake",
            model=model,
            provider_model_version=f"{model}-2026-08-24",
            provider_request_id="provider-request-1",
            usage=ReasoningUsage(
                input_tokens=100,
                cached_input_tokens=20,
                output_tokens=30,
            ),
            latency_ms=42,
            retry_count=0,
            estimated_cost=Decimal("0.000520"),
            currency="USD",
        )


class RecordingGatewayLogger:
    def __init__(self) -> None:
        self.warnings: list[dict[str, object]] = []

    def info(self, _event: str, **_metadata: object) -> None:
        pass

    def warning(self, event: str, **metadata: object) -> None:
        self.warnings.append({"event": event, **metadata})


class RecordingResponsesClient:
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


def settings(tmp_path: Path) -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text("COUNTERQ_APP_ENV=local\nOPENAI_API_KEY=test-key\n")
    return create_settings(env_file=env_file)


async def gateway_sessionmaker() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], DevelopmentInterview]
]:
    engine = build_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            async with session.begin():
                dev = await create_development_interview(session, initial_stage="IMPLEMENTATION")
        yield maker, dev
    finally:
        await engine.dispose()


def policy(instructions: str = "Smoke policy") -> ReasoningPolicyDescriptor:
    return ReasoningPolicyDescriptor(
        policy_key=f"stage1_6_policy_{policy_instruction_hash(instructions)[-12:]}",
        version="v1",
        instructions=instructions,
        configuration={"stage": "1.6"},
    )


async def call_gateway(
    tmp_path: Path,
    *,
    capability: str = "STANDARD_REASONING",
    provider: FakeReasoningProvider | None = None,
    timeout_seconds: float | None = None,
) -> tuple[AIGateway, FakeReasoningProvider, Any, DevelopmentInterview]:
    async for maker, dev in gateway_sessionmaker():
        fake_provider = provider or FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=fake_provider)
        result = await gateway.reason_structured(
            interview_session_id=dev.interview_session.id,
            capability=capability,  # type: ignore[arg-type]
            purpose="development_reasoning_smoke",
            policy=policy(),
            instructions="Smoke policy",
            input_content="Fixed smoke input",
            output_model=SmokeResult,
            timeout_seconds=timeout_seconds,
        )
        return gateway, fake_provider, result, dev
    raise AssertionError("sessionmaker fixture did not yield")


async def test_standard_and_strong_reasoning_route_to_configured_models(tmp_path: Path) -> None:
    _, standard_provider, standard, _ = await call_gateway(
        tmp_path,
        capability="STANDARD_REASONING",
    )
    _, strong_provider, strong, _ = await call_gateway(tmp_path, capability="STRONG_REASONING")

    assert standard.model == "gpt-5.6-terra"
    assert standard_provider.efforts == ["medium"]
    assert strong.model == "gpt-5.6-sol"
    assert strong_provider.efforts == ["high"]


async def test_strict_structured_output_success_and_invalid_output_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, result, _ = await call_gateway(tmp_path)
    assert result.parsed.verdict == "NOT_GUARANTEED"

    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider(output_data={"verdict": "RAW_PRIVATE_PROVIDER_OUTPUT"})
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        recording_logger = RecordingGatewayLogger()
        monkeypatch.setattr("app.ai_gateway.gateway.logger", recording_logger)
        with pytest.raises(StructuredOutputValidationFailure):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("invalid output policy"),
                instructions="invalid output policy",
                input_content="PRIVATE_CANDIDATE_INPUT",
                output_model=SmokeResult,
            )

        diagnostic = next(
            entry
            for entry in recording_logger.warnings
            if entry.get("event") == "reasoning_structured_output_invalid"
        )
        assert diagnostic["purpose"] == "development_reasoning_smoke"
        assert isinstance(diagnostic["policy_key"], str)
        assert diagnostic["policy_key"].startswith("stage1_6_policy_")
        assert diagnostic["policy_version"] == "v1"
        assert diagnostic["output_schema_name"] == "SmokeResult"
        assert diagnostic["validation_error_count"] == 2
        assert diagnostic["validation_error_types"] == ["missing"]
        assert diagnostic["validation_error_field_paths"] == ["technical_note", "confidence"]
        assert diagnostic["ai_invocation_id"]
        assert "RAW_PRIVATE_PROVIDER_OUTPUT" not in str(diagnostic)
        assert "PRIVATE_CANDIDATE_INPUT" not in str(diagnostic)


def test_strict_reasoning_schema_contains_required_additional_properties() -> None:
    from app.ai_gateway.routes import DevelopmentReasoningSmokeResult

    schema = DevelopmentReasoningSmokeResult.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["verdict", "technical_note", "confidence"]
    validate_strict_reasoning_schema(schema)


def test_nested_strict_reasoning_schema_is_valid() -> None:
    schema = StrictNestedResult.model_json_schema()

    assert schema["additionalProperties"] is False
    nested = schema["$defs"]["StrictNestedDetail"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["label"]
    validate_strict_reasoning_schema(schema)


@pytest.mark.parametrize(
    "model",
    [PermissiveResult, MissingRequiredResult, IncompatibleNestedResult],
)
def test_invalid_structured_output_models_fail_preflight(model: type[BaseModel]) -> None:
    with pytest.raises(StructuredOutputSchemaError):
        validate_strict_reasoning_schema(model.model_json_schema())


def test_top_level_anyof_schema_fails_preflight() -> None:
    with pytest.raises(StructuredOutputSchemaError):
        validate_strict_reasoning_schema(
            {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    }
                ]
            }
        )


async def test_gateway_schema_preflight_prevents_provider_invocation(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)

        with pytest.raises(StructuredOutputSchemaInvalid):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("schema preflight policy"),
                instructions="schema preflight policy",
                input_content="Fixed smoke input",
                output_model=PermissiveResult,
            )

        async with maker() as session:
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == dev.interview_session.id)
            )
            budget = await session.get(SessionBudget, dev.interview_session.id)

        assert provider.calls == 0
        assert invocation_count == 0
        assert budget is not None
        assert budget.deep_reasoning_used == 0


async def test_policy_version_hash_reuse_and_conflict(db_session: AsyncSession) -> None:
    descriptor = policy("immutable instructions")

    first = await get_or_create_policy_version(db_session, descriptor)
    second = await get_or_create_policy_version(db_session, descriptor)

    assert second.id == first.id
    assert first.prompt_hash == policy_instruction_hash("immutable instructions")

    with pytest.raises(PolicyVersionConflict):
        await get_or_create_policy_version(
            db_session,
            ReasoningPolicyDescriptor(
                policy_key=descriptor.policy_key,
                version=descriptor.version,
                instructions="changed instructions",
                configuration=descriptor.configuration,
            ),
        )


async def test_live_examiner_v9_and_v10_coexist_immutably_without_migration(
    db_session: AsyncSession,
) -> None:
    current_descriptor = live_examiner_policy_descriptor()
    old_configuration = {
        **current_descriptor.configuration,
        "policy_id": "live_examiner.v9",
        "context_projection_version": "v2",
    }
    old_descriptor = ReasoningPolicyDescriptor(
        policy_key=current_descriptor.policy_key,
        version="v9",
        instructions=LIVE_EXAMINER_INSTRUCTIONS,
        configuration=old_configuration,
        code_revision=current_descriptor.code_revision,
    )

    old_policy = await get_or_create_policy_version(db_session, old_descriptor)
    old_snapshot = (
        old_policy.id,
        old_policy.prompt_hash,
        dict(old_policy.configuration_json),
        old_policy.code_revision,
    )
    current_policy = await get_or_create_policy_version(db_session, current_descriptor)
    repeated_current_policy = await get_or_create_policy_version(
        db_session,
        current_descriptor,
    )

    assert current_descriptor.version == "v10"
    assert current_descriptor.configuration["policy_id"] == "live_examiner.v10"
    assert current_descriptor.configuration["context_projection_version"] == "v3"
    assert current_policy.id != old_policy.id
    assert repeated_current_policy.id == current_policy.id
    assert old_policy.prompt_hash == current_policy.prompt_hash
    assert old_policy.prompt_hash == policy_instruction_hash(LIVE_EXAMINER_INSTRUCTIONS)

    with pytest.raises(PolicyVersionConflict):
        await get_or_create_policy_version(
            db_session,
            ReasoningPolicyDescriptor(
                policy_key=current_descriptor.policy_key,
                version=current_descriptor.version,
                instructions=current_descriptor.instructions,
                configuration={
                    **current_descriptor.configuration,
                    "context_projection_version": "v2",
                },
                code_revision=current_descriptor.code_revision,
            ),
        )

    persisted = (
        await db_session.scalars(
            select(AIPolicyVersion).where(
                AIPolicyVersion.policy_key == current_descriptor.policy_key,
                AIPolicyVersion.version.in_(("v9", "v10")),
            )
        )
    ).all()
    by_version = {policy_version.version: policy_version for policy_version in persisted}

    assert set(by_version) == {"v9", "v10"}
    assert by_version["v9"].configuration_json == old_configuration
    assert by_version["v10"].configuration_json == current_descriptor.configuration
    assert (
        by_version["v9"].id,
        by_version["v9"].prompt_hash,
        dict(by_version["v9"].configuration_json),
        by_version["v9"].code_revision,
    ) == old_snapshot


async def test_ai_invocation_lifecycle_success_budget_and_cost(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        result = await gateway.reason_structured(
            interview_session_id=dev.interview_session.id,
            capability="STANDARD_REASONING",
            purpose="development_reasoning_smoke",
            policy=policy("success policy"),
            instructions="success policy",
            input_content="Fixed smoke input",
            output_model=SmokeResult,
        )

        async with maker() as session:
            invocation = await session.get(AIInvocation, result.invocation_id)
            budget = await session.get(SessionBudget, dev.interview_session.id)

        assert invocation is not None
        assert budget is not None
        assert invocation.status == "SUCCEEDED"
        assert invocation.completed_at is not None
        assert invocation.provider_request_id == "provider-request-1"
        assert invocation.input_tokens == 100
        assert invocation.cached_input_tokens == 20
        assert invocation.output_tokens == 30
        assert invocation.estimated_cost == Decimal("0.000520")
        assert invocation.currency == "USD"
        assert invocation.retry_count == 0
        assert budget.deep_reasoning_used == 1
        assert budget.strong_reasoning_used == 0
        assert budget.probes_used == 0
        assert budget.estimated_cost == Decimal("0.0005")


async def test_provider_failure_and_timeout_update_invocation_status(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider(
            error=ReasoningProviderError("RATE_LIMIT", "Provider rate limit reached")
        )
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)

        with pytest.raises(ReasoningProviderError):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("failure policy"),
                instructions="failure policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
        async with maker() as session:
            invocation = await session.scalar(
                select(AIInvocation).where(AIInvocation.error_class == "RATE_LIMIT")
            )
        assert invocation is not None
        assert invocation.status == "FAILED"

    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider(delay_seconds=0.05)
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        with pytest.raises(ReasoningProviderError):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("timeout policy"),
                instructions="timeout policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
                timeout_seconds=0.001,
            )
        async with maker() as session:
            invocation = await session.scalar(
                select(AIInvocation).where(AIInvocation.error_class == "TIMEOUT")
            )
        assert invocation is not None
        assert invocation.status == "TIMED_OUT"


async def test_cancellation_updates_invocation_status(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider(delay_seconds=1)
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        task = asyncio.create_task(
            gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("cancel policy"),
                instructions="cancel policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
        )
        await asyncio.wait_for(provider.called_event.wait(), timeout=1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        async with maker() as session:
            invocation = await session.scalar(
                select(AIInvocation).where(AIInvocation.error_class == "CANCELLED")
            )
        assert invocation is not None
        assert invocation.status == "CANCELLED"


async def test_provider_call_occurs_with_no_gateway_transaction(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        provider.assert_no_gateway_transaction = gateway

        await gateway.reason_structured(
            interview_session_id=dev.interview_session.id,
            capability="STANDARD_REASONING",
            purpose="development_reasoning_smoke",
            policy=policy("transaction boundary policy"),
            instructions="transaction boundary policy",
            input_content="Fixed smoke input",
            output_model=SmokeResult,
        )

        assert provider.calls == 1


async def test_exhausted_deep_reasoning_prevents_provider_invocation(
    tmp_path: Path,
) -> None:
    async for maker, dev in gateway_sessionmaker():
        async with maker() as session:
            async with session.begin():
                budget = await session.get(SessionBudget, dev.interview_session.id)
                assert budget is not None
                budget.deep_reasoning_used = budget.max_deep_reasoning_calls
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)

        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("exhausted deep policy"),
                instructions="exhausted deep policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
        assert provider.calls == 0


async def test_standard_reasoning_partition_preserves_live_capacity_then_uses_reserve(
    tmp_path: Path,
) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)

        for call_number in range(1, 9):
            result = await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="live_examiner",
                policy=policy("partition policy"),
                instructions="partition policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
            assert result.budget_used == call_number
            assert result.budget_remaining == 8 - call_number

        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("partition policy"),
                instructions="partition policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        for post_call_number in range(1, 17):
            result = await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                policy=policy("partition policy"),
                instructions="partition policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
            assert result.budget_used == 8 + post_call_number
            assert result.budget_remaining == 16 - post_call_number

        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                policy=policy("partition policy"),
                instructions="partition policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        async with maker() as session:
            budget = await session.get(SessionBudget, dev.interview_session.id)
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == dev.interview_session.id)
            )
        assert budget is not None
        assert budget.max_deep_reasoning_calls == 24
        assert budget.reserved_post_interview_deep_reasoning_calls == 16
        assert interactive_deep_reasoning_limit(budget) == 8
        assert budget_availability(budget).deep_reasoning_available is False
        assert budget.deep_reasoning_used == 24
        assert invocation_count == 24
        assert provider.calls == 24


async def test_post_interview_assessment_can_use_unspent_interactive_capacity(
    tmp_path: Path,
) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)

        for _ in range(24):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                policy=policy("post-only policy"),
                instructions="post-only policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                policy=policy("post-only policy"),
                instructions="post-only policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        assert provider.calls == 24


async def test_zero_reserve_preserves_legacy_standard_reasoning_limit(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        async with maker() as session, session.begin():
            budget = await session.get(SessionBudget, dev.interview_session.id)
            assert budget is not None
            budget.max_deep_reasoning_calls = 8
            budget.reserved_post_interview_deep_reasoning_calls = 0

        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        for _ in range(8):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="live_examiner",
                policy=policy("legacy reserve policy"),
                instructions="legacy reserve policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )
        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STANDARD_REASONING",
                purpose="live_examiner",
                policy=policy("legacy reserve policy"),
                instructions="legacy reserve policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        assert provider.calls == 8


async def test_hard_monetary_limit_blocks_every_reasoning_partition(tmp_path: Path) -> None:
    async for maker, dev in gateway_sessionmaker():
        async with maker() as session, session.begin():
            budget = await session.get(SessionBudget, dev.interview_session.id)
            assert budget is not None
            budget.estimated_cost = budget.hard_monetary_budget

        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        for capability, purpose_name in (
            ("STANDARD_REASONING", "live_examiner"),
            ("STANDARD_REASONING", POST_INTERVIEW_ASSESSMENT_PURPOSE),
            ("STRONG_REASONING", "live_examiner"),
        ):
            with pytest.raises(ReasoningBudgetExceeded):
                await gateway.reason_structured(
                    interview_session_id=dev.interview_session.id,
                    capability=capability,  # type: ignore[arg-type]
                    purpose=purpose_name,
                    policy=policy("hard limit all purposes"),
                    instructions="hard limit all purposes",
                    input_content="Fixed smoke input",
                    output_model=SmokeResult,
                )

        async with maker() as session:
            budget = await session.get(SessionBudget, dev.interview_session.id)
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == dev.interview_session.id)
            )
        assert budget is not None
        assert budget.deep_reasoning_used == 0
        assert budget.strong_reasoning_used == 0
        assert invocation_count == 0
        assert provider.calls == 0


async def test_strong_reasoning_counter_is_unchanged_by_standard_partition(
    tmp_path: Path,
) -> None:
    async for maker, dev in gateway_sessionmaker():
        provider = FakeReasoningProvider()
        gateway = AIGateway(settings=settings(tmp_path), sessionmaker=maker, provider=provider)
        await gateway.reason_structured(
            interview_session_id=dev.interview_session.id,
            capability="STRONG_REASONING",
            purpose="live_examiner",
            policy=policy("strong partition policy"),
            instructions="strong partition policy",
            input_content="Fixed smoke input",
            output_model=SmokeResult,
        )
        with pytest.raises(ReasoningBudgetExceeded):
            await gateway.reason_structured(
                interview_session_id=dev.interview_session.id,
                capability="STRONG_REASONING",
                purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                policy=policy("strong partition policy"),
                instructions="strong partition policy",
                input_content="Fixed smoke input",
                output_model=SmokeResult,
            )

        async with maker() as session:
            budget = await session.get(SessionBudget, dev.interview_session.id)
        assert budget is not None
        assert budget.deep_reasoning_used == 0
        assert budget.strong_reasoning_used == 1
        assert provider.calls == 1

def test_known_and_unknown_pricing() -> None:
    known = estimate_text_token_cost(
        "gpt-5.6-terra",
        ReasoningUsage(input_tokens=100, cached_input_tokens=20, output_tokens=30),
    )
    unknown = estimate_text_token_cost(
        "unknown-model",
        ReasoningUsage(input_tokens=100, output_tokens=30),
    )

    assert known == (Decimal("0.000524"), "USD")
    assert unknown is None


@pytest.mark.asyncio
async def test_openai_adapter_targets_responses_api_and_structured_output(
    tmp_path: Path,
) -> None:
    request = httpx.Request("POST", OPENAI_RESPONSES_URL)
    response = httpx.Response(
        200,
        json={
            "id": "resp-1",
            "model": "gpt-5.6-terra",
            "output_text": (
                '{"verdict":"NOT_GUARANTEED","technical_note":"Average case only.",'
                '"confidence":0.9}'
            ),
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 30,
            },
        },
        request=request,
    )
    client = RecordingResponsesClient(response)
    provider = OpenAIReasoningProvider(settings(tmp_path), http_client=client)

    result = await provider.reason_structured(
        ReasoningRequest(
            capability="STANDARD_REASONING",
            purpose="development_reasoning_smoke",
            policy=policy("openai adapter policy"),
            instructions="openai adapter policy",
            input_content="Fixed smoke input",
            output_schema_name="SmokeResult",
            output_json_schema=SmokeResult.model_json_schema(),
            timeout_seconds=5,
        ),
        model="gpt-5.6-terra",
        reasoning_effort="medium",
    )

    assert client.url == OPENAI_RESPONSES_URL
    assert client.json is not None
    assert client.json["input"] == "Fixed smoke input"
    assert client.json["reasoning"] == {"effort": "medium"}
    assert client.json["text"]["format"]["type"] == "json_schema"
    assert client.json["text"]["format"]["strict"] is True
    assert client.json["text"]["format"]["schema"]["additionalProperties"] is False
    assert client.json["text"]["format"]["schema"]["required"] == [
        "verdict",
        "technical_note",
        "confidence",
    ]
    assert result.output_data["verdict"] == "NOT_GUARANTEED"
    assert result.provider_request_id == "resp-1"


@pytest.mark.asyncio
async def test_provider_errors_do_not_expose_secret(tmp_path: Path) -> None:
    request = httpx.Request("POST", OPENAI_RESPONSES_URL)
    provider = OpenAIReasoningProvider(
        settings(tmp_path),
        http_client=RecordingResponsesClient(
            httpx.Response(401, json={"error": "bad"}, request=request)
        ),
    )

    with pytest.raises(ReasoningProviderError) as exc_info:
        await provider.reason_structured(
            ReasoningRequest(
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("secret-safe policy"),
                instructions="secret-safe policy",
                input_content="Fixed smoke input",
                output_schema_name="SmokeResult",
                output_json_schema=SmokeResult.model_json_schema(),
                timeout_seconds=5,
            ),
            model="gpt-5.6-terra",
            reasoning_effort="medium",
        )

    assert exc_info.value.category == "AUTHENTICATION"
    assert "test-key" not in exc_info.value.safe_message


@pytest.mark.asyncio
async def test_openai_error_diagnostics_are_sanitized(tmp_path: Path) -> None:
    request = httpx.Request("POST", OPENAI_RESPONSES_URL)
    provider = OpenAIReasoningProvider(
        settings(tmp_path),
        http_client=RecordingResponsesClient(
            httpx.Response(
                400,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_json_schema",
                        "param": "text.format.schema",
                        "message": "Invalid schema: additionalProperties is required.",
                    }
                },
                request=request,
            )
        ),
    )

    with pytest.raises(ReasoningProviderError) as exc_info:
        await provider.reason_structured(
            ReasoningRequest(
                capability="STANDARD_REASONING",
                purpose="development_reasoning_smoke",
                policy=policy("diagnostics policy"),
                instructions="diagnostics policy",
                input_content="Fixed smoke input",
                output_schema_name="SmokeResult",
                output_json_schema=SmokeResult.model_json_schema(),
                timeout_seconds=5,
            ),
            model="gpt-5.6-terra",
            reasoning_effort="medium",
        )

    error = exc_info.value
    assert error.category == "INVALID_REQUEST"
    assert error.provider_error_type == "invalid_request_error"
    assert error.provider_error_code == "invalid_json_schema"
    assert error.provider_error_param == "text.format.schema"
    assert error.safe_provider_message == "Invalid schema: additionalProperties is required."
    assert "test-key" not in error.safe_message


async def test_development_smoke_endpoint_blocks_production_and_creates_no_interpretations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_settings = settings(tmp_path)
    production_settings.app_env = "production"
    fake_provider = FakeReasoningProvider()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: production_settings
    app.dependency_overrides[get_reasoning_provider_builder] = lambda: (
        lambda _settings: fake_provider
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        blocked = await client.post(
            "/api/ai/development-reasoning-smoke",
            json={"interview_session_id": "018f0000-0000-7000-8000-000000000000"},
        )

    assert blocked.status_code == 403
    assert fake_provider.calls == 0
    app.dependency_overrides.clear()

    local_settings = settings(tmp_path)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: local_settings
    app.dependency_overrides[get_reasoning_provider_builder] = lambda: (
        lambda _settings: fake_provider
    )
    transport = ASGITransport(app=app)

    fixture_engine = build_engine()
    fixture_sessions = async_sessionmaker(fixture_engine, expire_on_commit=False)
    try:
        async def override_session() -> AsyncIterator[AsyncSession]:
            async with fixture_sessions() as fixture_session:
                yield fixture_session

        app.dependency_overrides[get_session] = override_session
        monkeypatch.setattr(
            "app.ai_gateway.routes.get_sessionmaker",
            lambda: fixture_sessions,
        )
        async with fixture_sessions() as fixture_session, fixture_session.begin():
            development = await create_development_interview(
                fixture_session,
                initial_stage="IMPLEMENTATION",
            )
            interview_session_id = development.interview_session.id

        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            bootstrap = await client.post(
                "/api/realtime/development-interview",
                json=RealtimeDevelopmentBootstrapRequest(
                    interview_session_id=interview_session_id,
                ).model_dump(mode="json"),
            )
            assert bootstrap.status_code == 200
            assert bootstrap.json()["restoration"] == "RESTORED"
            result = await client.post(
                "/api/ai/development-reasoning-smoke",
                json={"interview_session_id": str(interview_session_id)},
            )

        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "SUCCEEDED"
        assert body["model"] == "gpt-5.6-terra"
        assert "OPENAI_API_KEY" not in str(body)

        async with fixture_sessions() as session:
            invocation_count = await session.scalar(
                select(func.count())
                .select_from(AIInvocation)
                .where(AIInvocation.interview_session_id == interview_session_id)
            )
            claim_count = await session.scalar(
                select(func.count())
                .select_from(CandidateClaim)
                .where(CandidateClaim.interview_session_id == interview_session_id)
            )
            decision_count = await session.scalar(
                select(func.count())
                .select_from(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == interview_session_id)
            )
            prompt_count = await session.scalar(
                select(func.count())
                .select_from(InterviewerPrompt)
                .where(InterviewerPrompt.interview_session_id == interview_session_id)
            )
            policy_count = await session.scalar(
                select(func.count()).select_from(AIPolicyVersion)
            )
    finally:
        await fixture_engine.dispose()

    assert invocation_count == 1
    assert claim_count == 0
    assert decision_count == 0
    assert prompt_count == 0
    assert policy_count is not None and policy_count >= 1
    app.dependency_overrides.clear()
    await dispose_engine()

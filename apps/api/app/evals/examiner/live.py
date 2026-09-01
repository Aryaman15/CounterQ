from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn, cast

from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningProvider,
    ReasoningRequest,
)
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import Settings, create_settings
from app.evals.examiner.harness import (
    aggregate_results,
    evaluation_context_json,
    load_fixtures,
    score_fixture,
)
from app.evals.examiner.schema import (
    EvaluationCallMetrics,
    EvaluationFixture,
    EvaluationResult,
)
from app.examiner.analysis_schema import ExaminerAnalysisResult, ExaminerVerificationReason
from app.examiner.policy import LIVE_EXAMINER_INSTRUCTIONS, live_examiner_policy_descriptor
from app.examiner.reasoning_pipeline import (
    ExaminerReasoningTier,
    build_reasoning_input_payload,
    initial_reasoning_tier,
    next_strong_verification_reason,
    reasoning_route_for_tier,
    unresolved_consequential_challenge,
)

LIVE_EVALUATION_OUTPUT = Path("tmp/stage4-examiner-eval.json")


@dataclass(frozen=True)
class _CompletedCall:
    tier: ExaminerReasoningTier
    parsed: ExaminerAnalysisResult
    provider_result: ProviderReasoningResult


def _refuse_without_opt_in() -> NoReturn:
    raise RuntimeError("Refusing live evaluation: set COUNTERQ_STAGE4_LIVE_EVAL=1 explicitly")


async def evaluate_fixture(
    fixture: EvaluationFixture,
    *,
    provider: ReasoningProvider,
    settings: Settings,
) -> EvaluationResult:
    context_json = evaluation_context_json(fixture.input)
    initial_tier = initial_reasoning_tier(context_json)
    preliminary = await _reason_once(
        fixture=fixture,
        context_json=context_json,
        tier=initial_tier,
        provider=provider,
        settings=settings,
    )
    calls = [preliminary]
    final = preliminary
    verification_reason = next_strong_verification_reason(initial_tier, preliminary.parsed)
    if verification_reason is not None:
        final = await _reason_once(
            fixture=fixture,
            context_json=context_json,
            tier="STRONG",
            provider=provider,
            settings=settings,
            required_verification_reason=verification_reason,
            preliminary_analysis=preliminary.parsed,
        )
        calls.append(final)

    suppressed = len(calls) == 2 and unresolved_consequential_challenge(final.parsed)
    metrics = [_call_metrics(item) for item in calls]
    metadata: dict[str, object] = {
        "initial_reasoning_tier": initial_tier,
        "strong_escalation_occurred": len(calls) == 2,
        "verification_reason": verification_reason or "NONE",
        "preliminary_action": preliminary.parsed.decision.action if len(calls) == 2 else None,
        "preliminary_strategy": (
            preliminary.parsed.decision.proposed_probe_strategy if len(calls) == 2 else None
        ),
        "provider": final.provider_result.provider,
        "model": final.provider_result.model,
        "provider_model_version": final.provider_result.provider_model_version,
        "calls": [item.model_dump(mode="json") for item in metrics],
        "total_latency_ms": sum(item.latency_ms for item in metrics),
        "input_tokens": _complete_usage_sum(metrics, "input_tokens"),
        "cached_input_tokens": _complete_usage_sum(metrics, "cached_input_tokens"),
        "output_tokens": _complete_usage_sum(metrics, "output_tokens"),
        "estimated_cost": _complete_cost_sum(metrics),
        "currency": _single_currency(metrics),
    }
    return score_fixture(fixture, final.parsed, metadata=metadata, suppressed=suppressed)


async def run_calibration_batch(
    *,
    fixtures: list[EvaluationFixture],
    provider: ReasoningProvider,
    settings: Settings,
    output_path: Path | None = None,
) -> dict[str, object]:
    results = [
        await evaluate_fixture(fixture, provider=provider, settings=settings)
        for fixture in fixtures
    ]
    report: dict[str, object] = {
        "results": [item.model_dump(mode="json") for item in results],
        "aggregate": aggregate_results(results),
    }
    if output_path is not None:
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        serialized = json.dumps(report, indent=2)
        await asyncio.to_thread(output_path.write_text, serialized, encoding="utf-8")
    return report


async def run_live_evaluation() -> dict[str, object]:
    if os.environ.get("COUNTERQ_STAGE4_LIVE_EVAL") != "1":
        _refuse_without_opt_in()
    settings = create_settings()
    provider = build_reasoning_provider(settings)
    report = await run_calibration_batch(
        fixtures=load_fixtures(),
        provider=provider,
        settings=settings,
        output_path=LIVE_EVALUATION_OUTPUT,
    )
    for item in cast(list[object], report["results"]):
        result = EvaluationResult.model_validate(item)
        if (
            not result.action_correct
            or result.forbidden_strategy_used
            or result.obvious_answer_leakage
        ):
            print(
                f"FAIL {result.fixture_id}: action={result.actual_action} "
                f"strategy={result.actual_strategy}"
            )
    print(json.dumps(report["aggregate"], indent=2))
    return report


async def _reason_once(
    *,
    fixture: EvaluationFixture,
    context_json: dict[str, object],
    tier: ExaminerReasoningTier,
    provider: ReasoningProvider,
    settings: Settings,
    required_verification_reason: ExaminerVerificationReason | None = None,
    preliminary_analysis: ExaminerAnalysisResult | None = None,
) -> _CompletedCall:
    route = reasoning_route_for_tier(
        tier,
        standard_effort=settings.reasoning_standard_effort,
        strong_effort=settings.reasoning_strong_effort,
    )
    input_payload = build_reasoning_input_payload(
        context_json=context_json,
        tier=tier,
        required_verification_reason=required_verification_reason,
        preliminary_analysis=preliminary_analysis,
    )
    metadata: dict[str, object] = {
        "evaluation_fixture_id": fixture.fixture_id,
        "reasoning_tier": tier,
    }
    if required_verification_reason is not None:
        metadata["required_verification_reason"] = required_verification_reason
    request = ReasoningRequest(
        capability=route.capability,
        purpose=route.purpose,
        policy=live_examiner_policy_descriptor(),
        instructions=LIVE_EXAMINER_INSTRUCTIONS,
        input_content=json.dumps(input_payload, sort_keys=True, default=str),
        output_schema_name="ExaminerAnalysisResult",
        output_json_schema=ExaminerAnalysisResult.model_json_schema(),
        timeout_seconds=settings.reasoning_timeout_seconds,
        correlation_id=fixture.fixture_id,
        metadata=metadata,
    )
    provider_result = await asyncio.wait_for(
        provider.reason_structured(
            request,
            model=(
                settings.reasoning_strong_model
                if route.capability == "STRONG_REASONING"
                else settings.reasoning_standard_model
            ),
            reasoning_effort=route.reasoning_effort,
        ),
        timeout=settings.reasoning_timeout_seconds,
    )
    return _CompletedCall(
        tier=tier,
        parsed=ExaminerAnalysisResult.model_validate(provider_result.output_data),
        provider_result=provider_result,
    )


def _call_metrics(call: _CompletedCall) -> EvaluationCallMetrics:
    result = call.provider_result
    return EvaluationCallMetrics(
        reasoning_tier=call.tier,
        provider=result.provider,
        model=result.model,
        provider_model_version=result.provider_model_version,
        latency_ms=result.latency_ms,
        input_tokens=result.usage.input_tokens,
        cached_input_tokens=result.usage.cached_input_tokens,
        output_tokens=result.usage.output_tokens,
        estimated_cost=str(result.estimated_cost) if result.estimated_cost is not None else None,
        currency=result.currency,
    )


def _complete_usage_sum(
    metrics: list[EvaluationCallMetrics],
    field_name: Literal["input_tokens", "cached_input_tokens", "output_tokens"],
) -> int | None:
    if field_name == "input_tokens":
        values = [item.input_tokens for item in metrics]
    elif field_name == "cached_input_tokens":
        values = [item.cached_input_tokens for item in metrics]
    else:
        values = [item.output_tokens for item in metrics]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _complete_cost_sum(metrics: list[EvaluationCallMetrics]) -> str | None:
    values = [item.estimated_cost for item in metrics]
    if any(value is None for value in values):
        return None
    return str(
        sum((Decimal(value) for value in values if value is not None), Decimal("0"))
    )


def _single_currency(metrics: list[EvaluationCallMetrics]) -> str | None:
    currencies = {item.currency for item in metrics if item.currency is not None}
    return next(iter(currencies)) if len(currencies) == 1 else None


if __name__ == "__main__":
    asyncio.run(run_live_evaluation())

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn, cast

from pydantic import ValidationError

from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningProvider,
    ReasoningProviderError,
    ReasoningRequest,
)
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import REPOSITORY_ROOT, Settings, create_settings
from app.evals.examiner.harness import (
    aggregate_results,
    evaluation_context_json,
    load_fixtures,
    score_fixture,
    score_invalid_output,
    score_non_delivery,
)
from app.evals.examiner.schema import (
    EvaluationCallMetrics,
    EvaluationFixture,
    EvaluationResult,
)
from app.examiner.analysis_schema import ExaminerAnalysisResult, ExaminerVerificationReason
from app.examiner.context_projection import LIVE_EXAMINER_CONTEXT_PROJECTION_VERSION
from app.examiner.policy import (
    LIVE_EXAMINER_INSTRUCTIONS,
    LIVE_EXAMINER_POLICY_KEY,
    LIVE_EXAMINER_POLICY_VERSION,
    live_examiner_policy_descriptor,
)
from app.examiner.reasoning_pipeline import (
    STRONG_ESCALATION_MIN_REMAINING_SECONDS,
    ExaminerReasoningTier,
    build_reasoning_input_payload,
    initial_reasoning_tier,
    next_strong_verification_reason,
    reasoning_route_for_tier,
    unresolved_consequential_challenge,
)

LIVE_EVALUATION_OUTPUT = REPOSITORY_ROOT / "tmp" / "stage4-examiner-eval.json"
LIVE_EVALUATION_REPORT_SCHEMA_VERSION = "stage4-examiner-calibration-report.v3"
LIVE_EVALUATION_RUNNER_VERSION = "stage4c-live-runner.v3"
ProviderWaiter = Callable[
    [Awaitable[ProviderReasoningResult], float], Awaitable[ProviderReasoningResult]
]


@dataclass(frozen=True)
class _CompletedCall:
    tier: ExaminerReasoningTier
    parsed: ExaminerAnalysisResult
    provider_result: ProviderReasoningResult
    input_characters: int
    input_bytes: int


class _ReasoningDeadlineExceeded(TimeoutError):
    def __init__(self, metrics: EvaluationCallMetrics) -> None:
        super().__init__("Live Examiner calibration usefulness deadline expired")
        self.metrics = metrics


class _StructuredOutputInvalid(RuntimeError):
    def __init__(self, metrics: EvaluationCallMetrics) -> None:
        super().__init__("Live Examiner calibration returned invalid structured output")
        self.metrics = metrics


def _refuse_without_opt_in() -> NoReturn:
    raise RuntimeError("Refusing live evaluation: set COUNTERQ_STAGE4_LIVE_EVAL=1 explicitly")


async def evaluate_fixture(
    fixture: EvaluationFixture,
    *,
    provider: ReasoningProvider,
    settings: Settings,
    clock: Callable[[], float] = time.monotonic,
    wait_for_provider: ProviderWaiter | None = None,
) -> EvaluationResult:
    started_at = clock()
    usefulness_deadline = started_at + settings.live_examiner_usefulness_seconds
    context_json = evaluation_context_json(fixture.input)
    serialized_context = json.dumps(context_json, sort_keys=True, separators=(",", ":"))
    context_characters = len(serialized_context)
    context_bytes = len(serialized_context.encode("utf-8"))
    initial_tier = initial_reasoning_tier(context_json)
    waiter = wait_for_provider or _default_provider_waiter
    try:
        preliminary = await _reason_once(
            fixture=fixture,
            context_json=context_json,
            tier=initial_tier,
            provider=provider,
            settings=settings,
            usefulness_deadline=usefulness_deadline,
            clock=clock,
            wait_for_provider=waiter,
        )
    except _ReasoningDeadlineExceeded as exc:
        metadata = _evaluation_metadata(
            initial_tier=initial_tier,
            calls=[exc.metrics],
            started_at=started_at,
            usefulness_deadline=usefulness_deadline,
            clock=clock,
            settings=settings,
            context_characters=context_characters,
            context_bytes=context_bytes,
            deadline_outcome="INITIAL_TIMEOUT",
            final_status="DEADLINE_EXPIRED",
        )
        return score_non_delivery(
            fixture,
            metadata=metadata,
            technical_rationale=(
                "The initial reasoning call did not complete inside the shared usefulness "
                "window, so no candidate-visible recommendation is safe."
            ),
        )
    except _StructuredOutputInvalid as exc:
        metadata = _evaluation_metadata(
            initial_tier=initial_tier,
            calls=[exc.metrics],
            started_at=started_at,
            usefulness_deadline=usefulness_deadline,
            clock=clock,
            settings=settings,
            context_characters=context_characters,
            context_bytes=context_bytes,
            final_status="INVALID_OUTPUT",
        )
        return score_invalid_output(fixture, metadata=metadata)
    calls = [preliminary]
    final = preliminary
    verification_reason = next_strong_verification_reason(initial_tier, preliminary.parsed)
    if verification_reason is not None:
        remaining = usefulness_deadline - clock()
        if remaining < STRONG_ESCALATION_MIN_REMAINING_SECONDS:
            metadata = _evaluation_metadata(
                initial_tier=initial_tier,
                calls=[_call_metrics(preliminary)],
                started_at=started_at,
                usefulness_deadline=usefulness_deadline,
                clock=clock,
                settings=settings,
                context_characters=context_characters,
                context_bytes=context_bytes,
                verification_reason=verification_reason,
                preliminary=preliminary,
                deadline_outcome="INSUFFICIENT_STRONG_WINDOW",
                final_status="SUPPRESSED",
            )
            return score_fixture(
                fixture,
                preliminary.parsed,
                metadata=metadata,
                suppressed=True,
            )
        try:
            final = await _reason_once(
                fixture=fixture,
                context_json=context_json,
                tier="STRONG",
                provider=provider,
                settings=settings,
                usefulness_deadline=usefulness_deadline,
                clock=clock,
                wait_for_provider=waiter,
                required_verification_reason=verification_reason,
                preliminary_analysis=preliminary.parsed,
            )
            calls.append(final)
        except _ReasoningDeadlineExceeded as exc:
            metadata = _evaluation_metadata(
                initial_tier=initial_tier,
                calls=[_call_metrics(preliminary), exc.metrics],
                started_at=started_at,
                usefulness_deadline=usefulness_deadline,
                clock=clock,
                settings=settings,
                context_characters=context_characters,
                context_bytes=context_bytes,
                verification_reason=verification_reason,
                preliminary=preliminary,
                strong_escalation_occurred=True,
                deadline_outcome="STRONG_TIMEOUT",
                final_status="DEADLINE_EXPIRED",
            )
            return score_fixture(
                fixture,
                preliminary.parsed,
                metadata=metadata,
                suppressed=True,
            )
        except _StructuredOutputInvalid as exc:
            metadata = _evaluation_metadata(
                initial_tier=initial_tier,
                calls=[_call_metrics(preliminary), exc.metrics],
                started_at=started_at,
                usefulness_deadline=usefulness_deadline,
                clock=clock,
                settings=settings,
                context_characters=context_characters,
                context_bytes=context_bytes,
                verification_reason=verification_reason,
                preliminary=preliminary,
                strong_escalation_occurred=True,
                final_status="INVALID_OUTPUT",
            )
            return score_invalid_output(fixture, metadata=metadata)

    suppressed = len(calls) == 2 and unresolved_consequential_challenge(final.parsed)
    metrics = [_call_metrics(item) for item in calls]
    metadata = _evaluation_metadata(
        initial_tier=initial_tier,
        calls=metrics,
        started_at=started_at,
        usefulness_deadline=usefulness_deadline,
        clock=clock,
        settings=settings,
        context_characters=context_characters,
        context_bytes=context_bytes,
        verification_reason=verification_reason or "NONE",
        preliminary=preliminary if len(calls) == 2 else None,
        strong_escalation_occurred=len(calls) == 2,
        final_status="SUPPRESSED" if suppressed else "COMPLETED",
    )
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
        "metadata": _run_metadata(fixtures=fixtures, settings=settings),
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
    usefulness_deadline: float,
    clock: Callable[[], float],
    wait_for_provider: ProviderWaiter,
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
    input_content = json.dumps(input_payload, sort_keys=True, default=str)
    model = (
        settings.reasoning_strong_model
        if route.capability == "STRONG_REASONING"
        else settings.reasoning_standard_model
    )
    output_json_schema = ExaminerAnalysisResult.model_json_schema()
    remaining = usefulness_deadline - clock()
    timeout_seconds = min(settings.reasoning_timeout_seconds, max(0.0, remaining))
    request = ReasoningRequest(
        capability=route.capability,
        purpose=route.purpose,
        policy=live_examiner_policy_descriptor(),
        instructions=LIVE_EXAMINER_INSTRUCTIONS,
        input_content=input_content,
        output_schema_name="ExaminerAnalysisResult",
        output_json_schema=output_json_schema,
        timeout_seconds=timeout_seconds,
        correlation_id=fixture.fixture_id,
        metadata=metadata,
    )
    if timeout_seconds <= 0:
        raise _ReasoningDeadlineExceeded(
            _timeout_metrics(tier=tier, provider=provider, model=model, latency_ms=0)
        )
    call_started_at = clock()
    try:
        provider_result = await wait_for_provider(
            provider.reason_structured(
                request,
                model=model,
                reasoning_effort=route.reasoning_effort,
            ),
            timeout_seconds,
        )
    except ReasoningProviderError as exc:
        if exc.category != "STRUCTURED_OUTPUT_INVALID":
            raise
        elapsed_ms = max(0, int((clock() - call_started_at) * 1000))
        raise _StructuredOutputInvalid(
            _invalid_provider_metrics(
                tier=tier,
                provider=provider,
                model=model,
                latency_ms=elapsed_ms,
            )
        ) from exc
    except TimeoutError as exc:
        elapsed_ms = max(0, int((clock() - call_started_at) * 1000))
        raise _ReasoningDeadlineExceeded(
            _timeout_metrics(
                tier=tier,
                provider=provider,
                model=model,
                latency_ms=elapsed_ms,
            )
        ) from exc
    try:
        parsed = ExaminerAnalysisResult.model_validate(provider_result.output_data)
    except ValidationError as exc:
        raise _StructuredOutputInvalid(
            _provider_result_metrics(
                tier=tier,
                result=provider_result,
                status="INVALID_OUTPUT",
            )
        ) from exc
    completed = _CompletedCall(
        tier=tier,
        parsed=parsed,
        provider_result=provider_result,
        input_characters=len(input_content),
        input_bytes=len(input_content.encode("utf-8")),
    )
    if clock() >= usefulness_deadline:
        raise _ReasoningDeadlineExceeded(
            _call_metrics(completed, status="COMPLETED_AFTER_DEADLINE")
        )
    return completed


def _call_metrics(
    call: _CompletedCall,
    *,
    status: Literal["COMPLETED", "COMPLETED_AFTER_DEADLINE"] = "COMPLETED",
) -> EvaluationCallMetrics:
    return _provider_result_metrics(tier=call.tier, result=call.provider_result, status=status)


def _provider_result_metrics(
    *,
    tier: ExaminerReasoningTier,
    result: ProviderReasoningResult,
    status: Literal["COMPLETED", "COMPLETED_AFTER_DEADLINE", "INVALID_OUTPUT"],
) -> EvaluationCallMetrics:
    return EvaluationCallMetrics(
        reasoning_tier=tier,
        status=status,
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


def _invalid_provider_metrics(
    *,
    tier: ExaminerReasoningTier,
    provider: ReasoningProvider,
    model: str,
    latency_ms: int,
) -> EvaluationCallMetrics:
    return EvaluationCallMetrics(
        reasoning_tier=tier,
        status="INVALID_OUTPUT",
        provider=provider.provider_name,
        model=model,
        provider_model_version=None,
        latency_ms=latency_ms,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        currency=None,
    )


def _timeout_metrics(
    *,
    tier: ExaminerReasoningTier,
    provider: ReasoningProvider,
    model: str,
    latency_ms: int,
) -> EvaluationCallMetrics:
    return EvaluationCallMetrics(
        reasoning_tier=tier,
        status="TIMED_OUT",
        provider=provider.provider_name,
        model=model,
        provider_model_version=None,
        latency_ms=latency_ms,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        currency=None,
    )


async def _default_provider_waiter(
    awaitable: Awaitable[ProviderReasoningResult], timeout_seconds: float
) -> ProviderReasoningResult:
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


def _evaluation_metadata(
    *,
    initial_tier: ExaminerReasoningTier,
    calls: list[EvaluationCallMetrics],
    started_at: float,
    usefulness_deadline: float,
    clock: Callable[[], float],
    settings: Settings,
    context_characters: int,
    context_bytes: int,
    verification_reason: ExaminerVerificationReason = "NONE",
    preliminary: _CompletedCall | None = None,
    strong_escalation_occurred: bool = False,
    deadline_outcome: str = "NONE",
    final_status: str = "COMPLETED",
) -> dict[str, object]:
    completed_at = clock()
    final_call = calls[-1] if calls else None
    return {
        "initial_reasoning_tier": initial_tier,
        "strong_escalation_occurred": strong_escalation_occurred,
        "verification_reason": verification_reason,
        "preliminary_action": (
            preliminary.parsed.decision.action if preliminary is not None else None
        ),
        "preliminary_strategy": (
            preliminary.parsed.decision.proposed_probe_strategy
            if preliminary is not None
            else None
        ),
        "provider": final_call.provider if final_call is not None else None,
        "model": final_call.model if final_call is not None else None,
        "provider_model_version": (
            final_call.provider_model_version if final_call is not None else None
        ),
        "calls": [item.model_dump(mode="json") for item in calls],
        "total_latency_ms": sum(item.latency_ms for item in calls),
        "input_tokens": _complete_usage_sum(calls, "input_tokens"),
        "cached_input_tokens": _complete_usage_sum(calls, "cached_input_tokens"),
        "output_tokens": _complete_usage_sum(calls, "output_tokens"),
        "estimated_cost": _complete_cost_sum(calls),
        "currency": _single_currency(calls),
        "usefulness_deadline_seconds": settings.live_examiner_usefulness_seconds,
        "elapsed_reasoning_ms": max(0, int((completed_at - started_at) * 1000)),
        "remaining_usefulness_ms_at_completion": max(
            0, int((usefulness_deadline - completed_at) * 1000)
        ),
        "context_json_characters": context_characters,
        "context_json_bytes": context_bytes,
        "deadline_outcome": deadline_outcome,
        "final_status": final_status,
    }


def _run_metadata(*, fixtures: list[EvaluationFixture], settings: Settings) -> dict[str, object]:
    canonical_corpus = json.dumps(
        [fixture.model_dump(mode="json") for fixture in fixtures],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "report_schema_version": LIVE_EVALUATION_REPORT_SCHEMA_VERSION,
        "runner_version": LIVE_EVALUATION_RUNNER_VERSION,
        "policy_key": LIVE_EXAMINER_POLICY_KEY,
        "policy_version": LIVE_EXAMINER_POLICY_VERSION,
        "context_projection_version": LIVE_EXAMINER_CONTEXT_PROJECTION_VERSION,
        "fixture_count": len(fixtures),
        "canonical_corpus_sha256": hashlib.sha256(canonical_corpus.encode("utf-8")).hexdigest(),
        "standard_model": settings.reasoning_standard_model,
        "standard_effort": settings.reasoning_standard_effort,
        "strong_model": settings.reasoning_strong_model,
        "strong_effort": settings.reasoning_strong_effort,
        "usefulness_deadline_seconds": settings.live_examiner_usefulness_seconds,
        "git_revision": _git_revision(),
    }


def _git_revision() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


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

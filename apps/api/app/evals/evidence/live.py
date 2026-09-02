from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from app.ai_gateway.provider import ReasoningProvider, ReasoningProviderError, ReasoningRequest
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import REPOSITORY_ROOT, Settings, create_settings
from app.evals.evidence.harness import aggregate, empty_failure_score, load_fixtures, score_output
from app.evals.evidence.schema import EvidenceEvaluationFixture, EvidenceEvaluationResult
from app.evidence.assessment_schema import (
    ASSESSMENT_OUTPUT_CONTRACT_VERSION,
    AssessmentAnalysisResult,
)
from app.evidence.policy import (
    ASSESSMENT_EVALUATOR_INSTRUCTIONS,
    ASSESSMENT_EVALUATOR_POLICY_KEY,
    ASSESSMENT_EVALUATOR_POLICY_VERSION,
    ASSESSMENT_INPUT_CONTRACT_VERSION,
    assessment_evaluator_policy_descriptor,
)
from app.evidence.units import serialize_assessment_input

LIVE_EVALUATION_OUTPUT = REPOSITORY_ROOT / "tmp" / "stage5-evidence-eval.json"
REPORT_SCHEMA_VERSION = "stage5-evidence-calibration-report.v1"
RUNNER_VERSION = "stage5b-live-runner.v1"


def _refuse_without_opt_in() -> NoReturn:
    raise RuntimeError("Refusing live evaluation: set COUNTERQ_STAGE5_LIVE_EVAL=1 explicitly")


async def evaluate_fixture(
    fixture: EvidenceEvaluationFixture,
    *,
    provider: ReasoningProvider,
    settings: Settings,
) -> EvidenceEvaluationResult:
    effort = settings.reasoning_standard_effort
    input_content = serialize_assessment_input(fixture.model_input)
    request = ReasoningRequest(
        capability="STANDARD_REASONING",
        purpose="stage5_live_evaluation",
        policy=assessment_evaluator_policy_descriptor(),
        instructions=ASSESSMENT_EVALUATOR_INSTRUCTIONS,
        input_content=input_content,
        output_schema_name=AssessmentAnalysisResult.__name__,
        output_json_schema=AssessmentAnalysisResult.model_json_schema(),
        timeout_seconds=settings.reasoning_timeout_seconds,
        correlation_id=fixture.fixture_id,
        metadata={"evaluation_fixture_id": fixture.fixture_id},
    )
    try:
        provider_result = await provider.reason_structured(
            request,
            model=settings.reasoning_standard_model,
            reasoning_effort=settings.reasoning_standard_effort,  # type: ignore[arg-type]
        )
        parsed = AssessmentAnalysisResult.model_validate(provider_result.output_data)
    except (ReasoningProviderError, ValidationError) as exc:
        return EvidenceEvaluationResult(
            fixture_id=fixture.fixture_id,
            output=None,
            score=empty_failure_score(fixture),
            provider_status="FAILED",
            provider=provider.provider_name,
            model=settings.reasoning_standard_model,
            capability="STANDARD_REASONING",
            reasoning_effort=effort,
            latency_ms=None,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            estimated_cost=None,
            currency=None,
            error_category=getattr(exc, "category", "STRUCTURED_OUTPUT_INVALID"),
        )
    return EvidenceEvaluationResult(
        fixture_id=fixture.fixture_id,
        output=parsed,
        score=score_output(fixture, parsed),
        provider_status="SUCCEEDED",
        provider=provider_result.provider,
        model=provider_result.model,
        capability="STANDARD_REASONING",
        reasoning_effort=effort,
        latency_ms=provider_result.latency_ms,
        input_tokens=provider_result.usage.input_tokens,
        cached_input_tokens=provider_result.usage.cached_input_tokens,
        output_tokens=provider_result.usage.output_tokens,
        estimated_cost=(
            str(provider_result.estimated_cost)
            if provider_result.estimated_cost is not None
            else None
        ),
        currency=provider_result.currency,
        error_category=None,
    )


async def run_live_evaluation(output_path: Path = LIVE_EVALUATION_OUTPUT) -> dict[str, object]:
    if os.environ.get("COUNTERQ_STAGE5_LIVE_EVAL") != "1":
        _refuse_without_opt_in()
    settings = create_settings()
    fixtures = load_fixtures()
    provider = build_reasoning_provider(settings)
    outcomes = [
        await evaluate_fixture(fixture, provider=provider, settings=settings)
        for fixture in fixtures
    ]
    corpus_json = json.dumps(
        [fixture.model_dump(mode="json") for fixture in fixtures],
        sort_keys=True,
        separators=(",", ":"),
    )
    results = [outcome.model_dump(mode="json") for outcome in outcomes]
    report: dict[str, object] = {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "runner_version": RUNNER_VERSION,
            "assessment_policy_key": ASSESSMENT_EVALUATOR_POLICY_KEY,
            "assessment_policy_version": ASSESSMENT_EVALUATOR_POLICY_VERSION,
            "input_contract_version": ASSESSMENT_INPUT_CONTRACT_VERSION,
            "output_contract_version": ASSESSMENT_OUTPUT_CONTRACT_VERSION,
            "model": settings.reasoning_standard_model,
            "capability": "STANDARD_REASONING",
            "reasoning_effort": settings.reasoning_standard_effort,
            "corpus_digest": "sha256:" + hashlib.sha256(corpus_json.encode()).hexdigest(),
            "git_revision": _git_revision(),
        },
        "results": results,
        "aggregate": aggregate(results),
    }
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output_path.write_text, json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))
    return report


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    asyncio.run(run_live_evaluation())

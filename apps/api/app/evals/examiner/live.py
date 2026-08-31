from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import NoReturn

from app.ai_gateway.provider import ReasoningRequest
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import create_settings
from app.evals.examiner.harness import (
    aggregate_results,
    load_fixtures,
    model_input_json,
    score_fixture,
)
from app.examiner.analysis_schema import ExaminerAnalysisResult
from app.examiner.policy import LIVE_EXAMINER_INSTRUCTIONS, live_examiner_policy_descriptor


def _refuse_without_opt_in() -> NoReturn:
    raise RuntimeError("Refusing live evaluation: set COUNTERQ_STAGE4_LIVE_EVAL=1 explicitly")


async def run_live_evaluation() -> dict[str, object]:
    if os.environ.get("COUNTERQ_STAGE4_LIVE_EVAL") != "1":
        _refuse_without_opt_in()
    settings = create_settings()
    provider = build_reasoning_provider(settings)
    results = []
    for fixture in load_fixtures():
        request = ReasoningRequest(
            capability="STANDARD_REASONING",
            purpose="stage4_examiner_evaluation",
            policy=live_examiner_policy_descriptor(),
            instructions=LIVE_EXAMINER_INSTRUCTIONS,
            input_content=model_input_json(fixture.input),
            output_schema_name="ExaminerAnalysisResult",
            output_json_schema=ExaminerAnalysisResult.model_json_schema(),
            timeout_seconds=settings.reasoning_timeout_seconds,
            metadata={"evaluation_fixture_id": fixture.fixture_id},
        )
        result = await provider.reason_structured(
            request,
            model=settings.reasoning_standard_model,
            reasoning_effort=settings.reasoning_standard_effort,  # type: ignore[arg-type]
        )
        parsed = ExaminerAnalysisResult.model_validate(result.output_data)
        scored = score_fixture(
            fixture,
            parsed,
            metadata={
                "provider": result.provider,
                "model": result.model,
                "latency_ms": result.latency_ms,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "estimated_cost": str(result.estimated_cost)
                if result.estimated_cost is not None
                else None,
                "currency": result.currency,
            },
        )
        results.append(scored)
        if (
            not scored.action_correct
            or scored.forbidden_strategy_used
            or scored.obvious_answer_leakage
        ):
            print(
                f"FAIL {fixture.fixture_id}: action={scored.actual_action} "
                f"strategy={scored.actual_strategy}"
            )
    report: dict[str, object] = {
        "results": [item.model_dump() for item in results],
        "aggregate": aggregate_results(results),
    }
    output = Path("tmp/stage4-examiner-eval.json")
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))
    return report


if __name__ == "__main__":
    asyncio.run(run_live_evaluation())

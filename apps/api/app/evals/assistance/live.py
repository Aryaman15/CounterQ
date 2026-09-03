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

from app.ai_gateway.provider import ReasoningProviderError, ReasoningRequest
from app.ai_gateway.provider_factory import build_reasoning_provider
from app.config.settings import REPOSITORY_ROOT, create_settings
from app.interviews.assistance_policy import (
    COACH_ASSISTANCE_INSTRUCTIONS,
    COACH_ASSISTANCE_OUTPUT_CONTRACT,
    COACH_ASSISTANCE_POLICY_KEY,
    COACH_ASSISTANCE_POLICY_VERSION,
    CoachAssistanceOutput,
    coach_assistance_policy_descriptor,
)
from app.interviews.mode_policy import ModePolicy

FIXTURES_PATH = Path(__file__).with_name("fixtures.json")
OUTPUT_PATH = REPOSITORY_ROOT / "tmp" / "stage6a-assistance-eval.json"


def _refuse_without_opt_in() -> NoReturn:
    raise RuntimeError("Refusing live evaluation: set COUNTERQ_STAGE6A_LIVE_EVAL=1 explicitly")


async def run_live_evaluation() -> dict[str, object]:
    if os.environ.get("COUNTERQ_STAGE6A_LIVE_EVAL") != "1":
        _refuse_without_opt_in()
    settings = create_settings()
    provider = build_reasoning_provider(settings)
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    policy = ModePolicy()
    results: list[dict[str, object]] = []
    for fixture in fixtures:
        decision = policy.evaluate_assistance(
            mode=fixture["mode"],
            stage=fixture["stage"],
            time_pressure=fixture["time_pressure"],
            meaningful_attempt_exists=fixture["attempt"],
            gap_evidence_exists=fixture["gap"],
            highest_delivered_level=fixture["prior"],
            correctness_confirmation=fixture["confirm"],
            sufficient_independent_evidence=fixture["independent"],
        )
        if not decision.allowed or decision.next_hint_level is None:
            results.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "status": "POLICY_DENIED",
                    "scores": _non_generation_scores(fixture, policy),
                }
            )
            continue
        if (
            fixture.get("candidate_progressed_after_authorization") is True
            or fixture.get("assistance_requested", True) is False
        ):
            results.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "status": "POLICY_SUPPRESSED",
                    "scores": _non_generation_scores(fixture, policy),
                }
            )
            continue
        request = ReasoningRequest(
            capability="STANDARD_REASONING",
            purpose="stage6a_live_evaluation",
            policy=coach_assistance_policy_descriptor(),
            instructions=COACH_ASSISTANCE_INSTRUCTIONS,
            input_content=json.dumps(
                {
                    "trusted_system_context": {
                        "selected_hint_level": decision.next_hint_level,
                        "assistance_type": fixture.get(
                            "expected_assistance_type", decision.next_hint_level
                        ),
                        "target": "the candidate's current invariant",
                        "reference": fixture.get(
                            "trusted_reference",
                            "Maintain the required invariant across each state update.",
                        ),
                    },
                    "untrusted_candidate_context": {
                        "content": fixture.get(
                            "candidate_context",
                            "The current attempt has a validated diagnostic gap.",
                        ),
                        "authority": "NONE",
                    },
                },
                sort_keys=True,
            ),
            output_schema_name=CoachAssistanceOutput.__name__,
            output_json_schema=CoachAssistanceOutput.model_json_schema(),
            timeout_seconds=settings.reasoning_timeout_seconds,
            correlation_id=fixture["fixture_id"],
        )
        try:
            provider_result = await provider.reason_structured(
                request,
                model=settings.reasoning_standard_model,
                reasoning_effort=settings.reasoning_standard_effort,  # type: ignore[arg-type]
            )
            output = CoachAssistanceOutput.model_validate(provider_result.output_data)
            results.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "status": "SUCCEEDED",
                    "output": output.model_dump(mode="json"),
                    "scores": _score_output(
                        fixture,
                        hint_level=decision.next_hint_level,
                        prompt_text=output.prompt_text,
                        policy=policy,
                    ),
                    "latency_ms": provider_result.latency_ms,
                    "input_tokens": provider_result.usage.input_tokens,
                    "cached_input_tokens": provider_result.usage.cached_input_tokens,
                    "output_tokens": provider_result.usage.output_tokens,
                    "estimated_cost": (
                        str(provider_result.estimated_cost)
                        if provider_result.estimated_cost is not None
                        else None
                    ),
                    "currency": provider_result.currency,
                }
            )
        except (ReasoningProviderError, ValidationError) as exc:
            results.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "status": "FAILED",
                    "error_category": getattr(exc, "category", "STRUCTURED_OUTPUT_INVALID"),
                }
            )
    corpus = json.dumps(fixtures, sort_keys=True, separators=(",", ":"))
    report: dict[str, object] = {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "report_schema_version": "stage6a-assistance-calibration-report.v1",
            "runner_version": "stage6a-live-runner.v1",
            "policy_key": COACH_ASSISTANCE_POLICY_KEY,
            "policy_version": COACH_ASSISTANCE_POLICY_VERSION,
            "output_contract_version": COACH_ASSISTANCE_OUTPUT_CONTRACT,
            "model": settings.reasoning_standard_model,
            "capability": "STANDARD_REASONING",
            "reasoning_effort": settings.reasoning_standard_effort,
            "corpus_digest": "sha256:" + hashlib.sha256(corpus.encode()).hexdigest(),
            "git_revision": await asyncio.to_thread(_git_revision),
        },
        "results": results,
    }
    await asyncio.to_thread(OUTPUT_PATH.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(OUTPUT_PATH.write_text, json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"fixture_count": len(fixtures)}, indent=2))
    return report


def _score_output(
    fixture: dict[str, object],
    *,
    hint_level: str,
    prompt_text: str,
    policy: ModePolicy,
) -> dict[str, bool]:
    lower = prompt_text.casefold()
    word_count = len(prompt_text.split())
    word_limits = {
        "METACOGNITIVE": 32,
        "PROBLEM_NARROWING": 44,
        "CONCEPTUAL_HINT": 60,
        "STRUCTURAL_HINT": 80,
        "DIRECT_TEACHING": 120,
    }
    full_solution_markers = ("```", "complete solution", "copy this code", "full code")
    hidden_markers = (
        "interview pack",
        "hidden test",
        "internal confidence",
        "evidence record",
        "breakpoint",
        "modepolicy",
        "chain-of-thought",
    )
    required_terms = _string_list(fixture.get("required_terms", []))
    forbidden_terms = _string_list(fixture.get("forbidden_terms", []))
    target_terms = _string_list(fixture.get("target_terms", ["invariant"]))
    below_teaching = hint_level != "DIRECT_TEACHING"
    no_solution_leak = not (
        below_teaching and any(marker in lower for marker in full_solution_markers)
    )
    return {
        "technical_correctness": all(term in lower for term in required_terms)
        and not any(term in lower for term in forbidden_terms),
        "authorized_level_adherence": no_solution_leak and word_count <= word_limits[hint_level],
        "solution_leakage_below_allowed_level": no_solution_leak,
        "target_specificity": any(term in lower for term in target_terms),
        "unnecessary_over_help": word_count <= word_limits[hint_level],
        "simulation_mode_respected": fixture["mode"] == "COACH",
        "self_correction_restraint": not bool(
            fixture.get("candidate_progressed_after_authorization")
        ),
        "factual_clarification_boundary": policy.factual_clarification_allowed(
            str(fixture["mode"]), solution_directed=False
        )
        and not policy.factual_clarification_allowed(str(fixture["mode"]), solution_directed=True),
        "concise_interviewer_phrasing": len(prompt_text) <= 480,
        "no_hidden_policy_leakage": not any(marker in lower for marker in hidden_markers),
    }


def _non_generation_scores(fixture: dict[str, object], policy: ModePolicy) -> dict[str, bool]:
    return {
        "technical_correctness": True,
        "authorized_level_adherence": True,
        "solution_leakage_below_allowed_level": True,
        "target_specificity": True,
        "unnecessary_over_help": True,
        "simulation_mode_respected": True,
        "self_correction_restraint": True,
        "factual_clarification_boundary": policy.factual_clarification_allowed(
            str(fixture["mode"]), solution_directed=False
        )
        and not policy.factual_clarification_allowed(str(fixture["mode"]), solution_directed=True),
        "concise_interviewer_phrasing": True,
        "no_hidden_policy_leakage": True,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).casefold() for item in value]


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

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningProviderError,
    ReasoningRequest,
    ReasoningUsage,
)
from app.config.settings import Settings, create_settings
from app.evals.examiner.harness import load_fixtures
from app.evals.examiner.live import evaluate_fixture, run_calibration_batch
from app.evals.examiner.schema import EvaluationFixture
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.observation.models import InterviewEvent


class FakeCalibrationProvider:
    provider_name = "stage4c-fake"

    def __init__(
        self,
        outputs: list[dict[str, Any] | ReasoningProviderError],
        *,
        after_call: Callable[[int], None] | None = None,
    ) -> None:
        self.outputs = outputs
        self.after_call = after_call
        self.calls: list[tuple[ReasoningRequest, str, ReasoningEffort]] = []

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        call_number = len(self.calls) + 1
        if call_number > len(self.outputs):
            raise AssertionError("Calibration runner made an unexpected provider call")
        self.calls.append((request, model, reasoning_effort))
        if self.after_call is not None:
            self.after_call(call_number)
        output = self.outputs[call_number - 1]
        if isinstance(output, ReasoningProviderError):
            raise output
        return ProviderReasoningResult(
            output_data=output,
            provider=self.provider_name,
            model=model,
            provider_model_version=f"{model}-2026-09-01",
            provider_request_id=f"stage4c-{call_number}",
            usage=ReasoningUsage(input_tokens=100, cached_input_tokens=5, output_tokens=20),
            latency_ms=call_number * 10,
            retry_count=0,
            estimated_cost=Decimal("0.01"),
            currency="USD",
        )


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _fixture(fixture_id: str) -> EvaluationFixture:
    return next(item for item in load_fixtures() if item.fixture_id == fixture_id)


def _settings(tmp_path: Path) -> Settings:
    env_file = tmp_path / "stage4c.env"
    env_file.write_text(
        "OPENAI_API_KEY=fake-only\n"
        "COUNTERQ_REASONING_STANDARD_MODEL=stage4c-standard\n"
        "COUNTERQ_REASONING_STRONG_MODEL=stage4c-strong\n"
        "COUNTERQ_REASONING_STANDARD_EFFORT=medium\n"
        "COUNTERQ_REASONING_STRONG_EFFORT=high\n",
        encoding="utf-8",
    )
    return create_settings(env_file)


def _decision_metadata(
    *,
    verification_required: bool = False,
    verification_reason: str = "NONE",
) -> dict[str, object]:
    return {
        "target_ranking": {
            "technical_importance": "HIGH",
            "interpretation_confidence": "MEDIUM",
            "diagnostic_value": "HIGH",
            "current_evidence_gap": "HIGH",
            "candidate_commitment": "HIGH",
            "context_relevance": "HIGH",
            "freshness": "HIGH",
            "self_correction_likelihood": "LOW",
            "interruption_cost": "LOW",
            "duplicate_evidence": "LOW",
            "time_pressure": "LOW",
            "probe_fatigue": "LOW",
            "staleness_risk": "LOW",
        },
        "verification": {
            "required": verification_required,
            "reason": verification_reason,
        },
    }


def _output(
    action: str,
    *,
    strategy: str | None = None,
    target_kind: str = "NONE",
    verification_required: bool = False,
    verification_reason: str = "NONE",
    normalized_claim: str = "hash lookup is always guaranteed constant time",
) -> dict[str, Any]:
    claims: list[dict[str, object]] = []
    target_claim_index = None
    if target_kind == "CLAIM":
        claims.append(
            {
                "normalized_claim": normalized_claim,
                "claim_type": "COMPLEXITY",
                "verbatim_excerpt": None,
                "confidence": 0.9,
            }
        )
        target_claim_index = 0
    return {
        "claims": claims,
        "decision": {
            "action": action,
            "target_kind": target_kind,
            "target_claim_index": target_claim_index,
            "proposed_probe_strategy": strategy,
            "technical_rationale": "Bounded technical rationale for human adjudication.",
            "confidence": 0.9,
            "priority": 4,
            "urgency": 2,
            **_decision_metadata(
                verification_required=verification_required,
                verification_reason=verification_reason,
            ),
        },
    }


async def test_fast_case_uses_production_tier_effort_and_runtime_control(
    tmp_path: Path,
) -> None:
    provider = FakeCalibrationProvider([_output("ASK", target_kind="CLAIM")])
    result = await evaluate_fixture(
        _fixture("container-water-ask-objective"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=MutableClock(),
    )

    request, model, effort = provider.calls[0]
    assert result.initial_reasoning_tier == "FAST"
    assert result.actual_action == "ASK" and result.action_correct
    assert request.capability == "STANDARD_REASONING"
    assert request.purpose == "live_examiner"
    assert model == "stage4c-standard" and effort == "low"
    assert request.timeout_seconds == 8.0
    assert result.deadline_outcome == "NONE"
    assert result.usefulness_deadline_seconds == 8.0
    assert result.remaining_usefulness_ms_at_completion == 8000
    assert json.loads(request.input_content)["trusted_runtime_control"] == {
        "reasoning_tier": "FAST",
        "verification_pass": "NONE",
        "this_is_single_verification_pass": False,
    }


async def test_medium_case_uses_production_tier_effort(tmp_path: Path) -> None:
    provider = FakeCalibrationProvider(
        [_output("PROBE", strategy="PROVE", target_kind="CODE_SNAPSHOT")]
    )
    result = await evaluate_fixture(
        _fixture("longest-substring-invariant-prove"),
        provider=provider,
        settings=_settings(tmp_path),
    )

    request, model, effort = provider.calls[0]
    assert result.initial_reasoning_tier == "MEDIUM"
    assert result.actual_action == "PROBE" and result.action_correct
    assert request.capability == "STANDARD_REASONING"
    assert model == "stage4c-standard" and effort == "medium"


async def test_strong_escalation_scores_final_result_and_retains_preliminary_call(
    tmp_path: Path,
) -> None:
    clock = MutableClock()

    def advance_after_initial(call_number: int) -> None:
        if call_number == 1:
            clock.value = 3.0

    provider = FakeCalibrationProvider(
        [
            _output(
                "PROBE",
                strategy="WHY",
                target_kind="CLAIM",
                verification_required=True,
                verification_reason="DIFFICULT_CODE_SEMANTICS",
            ),
            _output("PROBE", strategy="ASSUMPTION_CHALLENGE", target_kind="CLAIM"),
        ],
        after_call=advance_after_initial,
    )
    result = await evaluate_fixture(
        _fixture("two-sum-hash-assumption"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=clock,
    )

    assert len(provider.calls) == 2
    assert result.strong_escalation_occurred
    assert result.verification_reason == "DIFFICULT_CODE_SEMANTICS"
    assert result.preliminary_action == "PROBE" and result.preliminary_strategy == "WHY"
    assert result.final_action == "PROBE"
    assert result.final_strategy == "ASSUMPTION_CHALLENGE"
    assert result.actual_strategy == "ASSUMPTION_CHALLENGE"
    assert result.action_correct and result.strategy_acceptable
    assert result.deadline_outcome == "NONE"
    assert [call.reasoning_tier for call in result.calls] == ["MEDIUM", "STRONG"]
    strong_request, strong_model, strong_effort = provider.calls[1]
    assert strong_request.capability == "STRONG_REASONING"
    assert strong_request.purpose == "live_examiner_strong_verification"
    assert strong_model == "stage4c-strong" and strong_effort == "high"
    assert strong_request.timeout_seconds == 5.0
    assert result.remaining_usefulness_ms_at_completion == 5000
    control = json.loads(strong_request.input_content)["trusted_runtime_control"]
    assert control == {
        "reasoning_tier": "STRONG",
        "verification_pass": "ONE_AND_ONLY",
        "this_is_single_verification_pass": True,
        "verification_reason": "DIFFICULT_CODE_SEMANTICS",
        "preliminary_recommendation": {
            "action": "PROBE",
            "target": {
                "kind": "CLAIM",
                "claim_type": "COMPLEXITY",
                "normalized_claim": "hash lookup is always guaranteed constant time",
            },
            "strategy": "WHY",
        },
        "verification_requirements": {
            "resolve_uncertainty_independently_using_original_context": True,
            "do_not_escalate_again": True,
            "if_unresolved": {
                "prefer_safe_neutral_action": ["WAIT", "OBSERVE"],
                "do_not_make_consequential_accusation": True,
            },
        },
    }


async def test_initial_invalid_output_is_reported_and_batch_continues(
    tmp_path: Path,
) -> None:
    invalid = _output("PROBE", strategy="WHY", target_kind="CLAIM")
    invalid["decision"]["action"] = "WAIT"
    fixtures = load_fixtures()
    provider = FakeCalibrationProvider(
        [invalid, *[_output("WAIT") for _ in range(len(fixtures) - 1)]]
    )
    output_path = tmp_path / "stage4-invalid-output-report.json"

    report = await run_calibration_batch(
        fixtures=fixtures,
        provider=provider,
        settings=_settings(tmp_path),
        output_path=output_path,
    )

    results = cast(list[dict[str, Any]], report["results"])
    invalid_result, completed_result = results[:2]
    assert len(provider.calls) == 24
    assert len(results) == 24
    assert output_path.exists()
    assert invalid_result["actual_action"] == "SUPPRESSED"
    assert invalid_result["final_status"] == "INVALID_OUTPUT"
    assert invalid_result["candidate_facing_prompt"] == ""
    assert invalid_result["action_correct"] is False
    assert invalid_result["calls"][0]["status"] == "INVALID_OUTPUT"
    assert invalid_result["estimated_cost"] == "0.01"
    assert completed_result["final_status"] == "COMPLETED"
    aggregate = cast(dict[str, Any], report["aggregate"])
    assert aggregate["structured_output_invalid"] == {
        "numerator": 1,
        "denominator": 24,
        "rate": pytest.approx(1 / 24),
    }


async def test_provider_invalid_output_is_reported_but_other_errors_propagate(
    tmp_path: Path,
) -> None:
    invalid_provider = FakeCalibrationProvider(
        [ReasoningProviderError("STRUCTURED_OUTPUT_INVALID", "Invalid provider output")]
    )
    result = await evaluate_fixture(
        _fixture("maximum-subarray-shallow-why"),
        provider=invalid_provider,
        settings=_settings(tmp_path),
        clock=MutableClock(),
    )

    assert result.final_status == "INVALID_OUTPUT"
    assert result.actual_action == "SUPPRESSED"
    assert result.calls[0].status == "INVALID_OUTPUT"
    assert result.estimated_cost is None
    assert len(invalid_provider.calls) == 1

    authentication_failure = FakeCalibrationProvider(
        [ReasoningProviderError("AUTHENTICATION", "Authentication failed")]
    )
    with pytest.raises(ReasoningProviderError, match="Authentication failed"):
        await evaluate_fixture(
            _fixture("maximum-subarray-shallow-why"),
            provider=authentication_failure,
            settings=_settings(tmp_path),
            clock=MutableClock(),
        )
    assert len(authentication_failure.calls) == 1


async def test_invalid_strong_output_suppresses_preliminary_probe_without_retry(
    tmp_path: Path,
) -> None:
    preliminary = _output(
        "PROBE",
        strategy="WHY",
        target_kind="CLAIM",
        verification_required=True,
        verification_reason="DIFFICULT_CODE_SEMANTICS",
    )
    invalid_strong = _output("PROBE", strategy="PROVE", target_kind="CLAIM")
    invalid_strong["decision"]["action"] = "OBSERVE"
    provider = FakeCalibrationProvider([preliminary, invalid_strong])

    result = await evaluate_fixture(
        _fixture("maximum-subarray-shallow-why"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=MutableClock(),
    )

    assert len(provider.calls) == 2
    assert result.strong_escalation_occurred
    assert result.preliminary_action == "PROBE"
    assert result.preliminary_strategy == "WHY"
    assert result.final_action is None and result.final_strategy is None
    assert result.final_status == "INVALID_OUTPUT"
    assert result.actual_action == "SUPPRESSED"
    assert result.candidate_facing_prompt == ""
    assert not result.action_correct
    assert [call.status for call in result.calls] == ["COMPLETED", "INVALID_OUTPUT"]
    assert result.estimated_cost == "0.02"


async def test_unresolved_strong_probe_is_suppressed_after_one_escalation(
    tmp_path: Path,
) -> None:
    unresolved = _output(
        "PROBE",
        strategy="ASSUMPTION_CHALLENGE",
        target_kind="CLAIM",
        verification_required=True,
        verification_reason="CONSEQUENTIAL_LOW_CONFIDENCE",
    )
    provider = FakeCalibrationProvider([unresolved, unresolved])
    result = await evaluate_fixture(
        _fixture("two-sum-hash-assumption"),
        provider=provider,
        settings=_settings(tmp_path),
    )

    assert len(provider.calls) == 2
    assert result.actual_action == "SUPPRESSED"
    assert result.actual_strategy is None
    assert result.final_action == "PROBE"
    assert result.final_strategy == "ASSUMPTION_CHALLENGE"
    assert result.final_status == "SUPPRESSED"
    assert not result.action_correct and result.strategy_acceptable is False
    assert result.candidate_facing_prompt == ""


async def test_initial_call_timeout_is_safe_non_delivery(tmp_path: Path) -> None:
    provider = FakeCalibrationProvider([_output("PROBE", strategy="WHY", target_kind="CLAIM")])

    async def timeout_waiter(
        awaitable: Awaitable[ProviderReasoningResult], timeout_seconds: float
    ) -> ProviderReasoningResult:
        assert timeout_seconds == 8.0
        await awaitable
        raise TimeoutError

    result = await evaluate_fixture(
        _fixture("maximum-subarray-shallow-why"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=MutableClock(),
        wait_for_provider=timeout_waiter,
    )

    assert len(provider.calls) == 1
    assert result.actual_action == "SUPPRESSED"
    assert result.final_action is None
    assert result.final_status == "DEADLINE_EXPIRED"
    assert result.deadline_outcome == "INITIAL_TIMEOUT"
    assert result.candidate_facing_prompt == ""
    assert result.calls[0].status == "TIMED_OUT"


async def test_insufficient_remaining_window_skips_strong_and_suppresses(
    tmp_path: Path,
) -> None:
    clock = MutableClock()

    def advance_after_initial(call_number: int) -> None:
        if call_number == 1:
            clock.value = 6.5

    provider = FakeCalibrationProvider(
        [
            _output(
                "PROBE",
                strategy="WHY",
                target_kind="CLAIM",
                verification_required=True,
                verification_reason="DIFFICULT_CODE_SEMANTICS",
            )
        ],
        after_call=advance_after_initial,
    )
    result = await evaluate_fixture(
        _fixture("maximum-subarray-shallow-why"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=clock,
    )

    assert len(provider.calls) == 1
    assert not result.strong_escalation_occurred
    assert result.actual_action == "SUPPRESSED"
    assert result.final_status == "SUPPRESSED"
    assert result.deadline_outcome == "INSUFFICIENT_STRONG_WINDOW"
    assert result.remaining_usefulness_ms_at_completion == 1500


async def test_strong_timeout_is_safely_suppressed(tmp_path: Path) -> None:
    provider = FakeCalibrationProvider(
        [
            _output(
                "PROBE",
                strategy="WHY",
                target_kind="CLAIM",
                verification_required=True,
                verification_reason="DIFFICULT_CODE_SEMANTICS",
            ),
            _output("PROBE", strategy="WHY", target_kind="CLAIM"),
        ]
    )
    calls = 0

    async def second_call_timeout(
        awaitable: Awaitable[ProviderReasoningResult], timeout_seconds: float
    ) -> ProviderReasoningResult:
        nonlocal calls
        calls += 1
        result = await awaitable
        if calls == 2:
            raise TimeoutError
        return result

    result = await evaluate_fixture(
        _fixture("maximum-subarray-shallow-why"),
        provider=provider,
        settings=_settings(tmp_path),
        clock=MutableClock(),
        wait_for_provider=second_call_timeout,
    )

    assert len(provider.calls) == 2
    assert result.strong_escalation_occurred
    assert result.actual_action == "SUPPRESSED"
    assert result.final_status == "DEADLINE_EXPIRED"
    assert result.deadline_outcome == "STRONG_TIMEOUT"
    assert [call.status for call in result.calls] == ["COMPLETED", "TIMED_OUT"]


async def test_safe_actions_never_escalate_only_because_verification_is_requested(
    tmp_path: Path,
) -> None:
    for action in ("WAIT", "OBSERVE", "ASK"):
        provider = FakeCalibrationProvider(
            [
                _output(
                    action,
                    target_kind="CLAIM" if action == "ASK" else "NONE",
                    verification_required=True,
                    verification_reason="CONSEQUENTIAL_LOW_CONFIDENCE",
                )
            ]
        )
        result = await evaluate_fixture(
            _fixture("weak-candidate-restraint"),
            provider=provider,
            settings=_settings(tmp_path),
            clock=MutableClock(),
        )
        assert len(provider.calls) == 1
        assert not result.strong_escalation_occurred
        assert result.verification_reason == "NONE"
        assert result.actual_action == action


async def test_calibration_runner_does_not_mutate_interview_history(
    tmp_path: Path,
    db_session: AsyncSession,
) -> None:
    before = (
        await db_session.scalar(select(func.count()).select_from(CandidateClaim)),
        await db_session.scalar(select(func.count()).select_from(ExaminerDecision)),
        await db_session.scalar(select(func.count()).select_from(InterviewEvent)),
    )
    provider = FakeCalibrationProvider([_output("ASK", target_kind="CLAIM")])
    await evaluate_fixture(
        _fixture("container-water-ask-objective"),
        provider=provider,
        settings=_settings(tmp_path),
    )
    after = (
        await db_session.scalar(select(func.count()).select_from(CandidateClaim)),
        await db_session.scalar(select(func.count()).select_from(ExaminerDecision)),
        await db_session.scalar(select(func.count()).select_from(InterviewEvent)),
    )
    assert after == before


async def test_report_contains_per_call_data_and_operational_aggregates(
    tmp_path: Path,
) -> None:
    provider = FakeCalibrationProvider(
        [
            _output("ASK", target_kind="CLAIM"),
            _output("PROBE", strategy="PROVE", target_kind="CODE_SNAPSHOT"),
        ]
    )
    output_path = tmp_path / "stage4-examiner-eval.json"
    report = await run_calibration_batch(
        fixtures=[
            _fixture("container-water-ask-objective"),
            _fixture("longest-substring-invariant-prove"),
        ],
        provider=provider,
        settings=_settings(tmp_path),
        output_path=output_path,
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == report
    result = cast(list[dict[str, Any]], report["results"])[0]
    assert {
        "fixture_id",
        "expected_action",
        "actual_action",
        "expected_strategies",
        "actual_strategy",
        "actual_target_kind",
        "initial_reasoning_tier",
        "strong_escalation_occurred",
        "verification_reason",
        "preliminary_action",
        "preliminary_strategy",
        "final_action",
        "final_strategy",
        "provider",
        "model",
        "provider_model_version",
        "calls",
        "total_latency_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "estimated_cost",
        "action_correct",
        "strategy_acceptable",
        "unnecessary_probe",
        "obvious_answer_leakage",
        "duplicate_probe_violation",
        "stale_behavior_violation",
        "manual_technical_review_required",
        "candidate_specificity_review_required",
        "technical_rationale",
        "candidate_facing_prompt",
        "deadline_outcome",
        "preferred_action_correct",
        "usefulness_deadline_seconds",
        "elapsed_reasoning_ms",
        "remaining_usefulness_ms_at_completion",
        "context_json_characters",
        "context_json_bytes",
    } <= set(result)
    calls = cast(list[dict[str, Any]], result["calls"])
    assert set(calls[0]) == {
        "reasoning_tier",
        "status",
        "provider",
        "model",
        "provider_model_version",
        "latency_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "estimated_cost",
        "currency",
    }
    aggregate = cast(dict[str, Any], report["aggregate"])
    metadata = cast(dict[str, Any], report["metadata"])
    assert metadata["report_schema_version"] == "stage4-examiner-calibration-report.v4"
    assert metadata["runner_version"] == "stage4c-live-runner.v4"
    assert metadata["policy_key"] == "live_examiner"
    assert metadata["policy_version"] == "v9"
    assert metadata["output_contract_version"] == "v2"
    assert metadata["context_projection_version"] == "v3"
    assert metadata["fixture_count"] == 2
    assert len(metadata["canonical_corpus_sha256"]) == 64
    assert metadata["standard_model"] == "stage4c-standard"
    assert metadata["strong_model"] == "stage4c-strong"
    assert metadata["usefulness_deadline_seconds"] == 8.0
    assert metadata["git_revision"]
    assert aggregate["initial_reasoning_tiers"] == {
        "FAST": {"count": 1, "rate": 0.5},
        "MEDIUM": {"count": 1, "rate": 0.5},
    }
    assert aggregate["strong_escalation"] == {
        "numerator": 0,
        "denominator": 2,
        "rate": 0.0,
    }
    assert aggregate["structured_output_invalid"] == {
        "numerator": 0,
        "denominator": 2,
        "rate": 0.0,
    }
    assert aggregate["average_total_latency_ms"] == 15
    assert aggregate["p95_total_latency_ms"] == 20
    assert aggregate["total_tokens"] == {
        "input_tokens": 200,
        "cached_input_tokens": 10,
        "output_tokens": 40,
    }
    assert aggregate["total_estimated_cost"] == "0.02"
    assert aggregate["currency"] == "USD"
    assert aggregate["context_size"]["average_characters"] > 0
    assert aggregate["context_size"]["p95_bytes"] > 0

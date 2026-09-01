from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.evals.examiner.schema import EvaluationFixture, EvaluationInput, EvaluationResult
from app.examiner.analysis_schema import ExaminerAnalysisResult
from app.examiner.context import (
    serialize_examiner_context,
    serialize_source_freshness,
    serialize_source_observation,
)
from app.examiner.context_contract import (
    ExaminerDiagnosticContext,
    serialize_diagnostic_context,
)
from app.interviews.prompt_authorization import compose_candidate_safe_prompt

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[3] / "evals" / "examiner"
LABEL_KEYS = frozenset({"expectations", "review", "label_sentinel", "must_not_reveal"})


def load_fixtures(directory: Path = FIXTURE_DIRECTORY) -> list[EvaluationFixture]:
    return [
        EvaluationFixture.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def evaluation_context_json(value: EvaluationInput) -> dict[str, object]:
    """Create the exact production context sections from evaluation-only input."""
    transcript: dict[str, object] | None = None
    if value.source_observation_type == "CANDIDATE_TRANSCRIPT_FINALIZED":
        transcript = {
            "transcript_segment_id": "evaluation-transcript",
            "text": value.candidate_statement or "\n".join(value.recent_transcript),
            "provider_confidence": value.candidate_statement_provider_confidence,
            "associated_code_snapshot_id": None,
            "associated_code_snapshot_version": None,
        }
    code: dict[str, object] | None = None
    if value.code_snapshot is not None:
        code = {
            "code_snapshot_id": "evaluation-code-snapshot",
            "code_snapshot_version": 1,
            "content_hash": "evaluation-only",
            "source_code": value.code_snapshot,
            "code_diff_id": "evaluation-code-diff" if value.code_diff else None,
            "code_diff_content": value.code_diff,
        }
    is_code = value.source_observation_type == "CODE_MEANINGFULLY_CHANGED"
    source = serialize_source_observation(
        kind=value.source_observation_type,
        source_event_id="evaluation-source-event",
        source_event_watermark=value.time_context.source_event_watermark,
        source_state_version=value.time_context.source_state_version,
        source_stage=value.state,
        trigger_class="CODE_EDIT_BURST" if is_code else "VOICE_TURN_COMPLETED",
        occurred_at="2000-01-01T00:00:00+00:00",
        transcript=transcript,
        code=code,
    )
    diagnostic_context = ExaminerDiagnosticContext(
        remaining_probe_budget=value.remaining_probe_budget,
        recent_transcript=value.recent_transcript,
        execution_context=value.execution_context,
        recent_claims=value.recent_claims,
        recent_delivered_prompt_intents=value.recent_delivered_prompt_intents,
        synthetic_prior_context=value.evaluation_context_extension,
    )
    pack = value.interview_pack
    return serialize_examiner_context(
        trusted_policy={
            "simulation_no_hints": value.mode == "SIMULATION",
            "candidate_content_is_untrusted_data": True,
            "model_recommends_only": True,
        },
        interview={
            "interview_session_id": "evaluation-session",
            "mode": value.mode,
            "candidate_level": value.candidate_level,
            "language": "cpp",
            "current_stage": value.state,
            "status": "ACTIVE",
            "state_version": value.time_context.current_state_version,
            "source_state_version": value.time_context.source_state_version,
            "source_event_watermark": value.time_context.source_event_watermark,
            "remaining_seconds": value.time_context.remaining_seconds,
        },
        problem=value.problem_context.model_dump(mode="json"),
        interview_pack={
            "interview_pack_version_id": "evaluation-pack",
            "schema_version": pack.get("schema_version"),
            "review_status": pack.get("review_status"),
            "pack": pack,
        },
        source_observation=source,
        source_freshness=serialize_source_freshness(
            latest_code_snapshot_id="evaluation-code-snapshot" if value.code_snapshot else None,
            latest_code_snapshot_version=1 if value.code_snapshot else None,
            is_latest_code_snapshot=(
                value.code_snapshot is not None
                and not value.time_context.newer_code_snapshot_exists
            ),
            newer_code_snapshot_exists=value.time_context.newer_code_snapshot_exists,
            newer_candidate_transcript_exists=(
                value.time_context.newer_candidate_transcript_exists
            ),
        ),
        recent_history=[
            {
                "event_id": "evaluation-history",
                "server_sequence": value.time_context.source_event_watermark,
                "event_type": "MEANINGFUL_CODE_CHANGE" if is_code else "TRANSCRIPT_FINALIZED",
                "source": "NATIVE_EDITOR" if is_code else "CANDIDATE_VOICE",
                "state_version": value.time_context.source_state_version,
                "code_snapshot_id": "evaluation-code-snapshot" if is_code else None,
                "payload_keys": ["interview_stage"],
            }
        ],
        diagnostic_context=serialize_diagnostic_context(diagnostic_context),
    )


def model_input_json(value: EvaluationInput) -> str:
    return json.dumps(evaluation_context_json(value), sort_keys=True, separators=(",", ":"))


def serialized_input_has_labels(serialized: str, fixture: EvaluationFixture) -> bool:
    parsed = json.loads(serialized)
    return fixture.expectations.label_sentinel in serialized or _has_label_key(parsed)


def _has_label_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(key in LABEL_KEYS or _has_label_key(item) for key, item in value.items())
    return isinstance(value, list) and any(_has_label_key(item) for item in value)


def score_fixture(
    fixture: EvaluationFixture,
    output: ExaminerAnalysisResult,
    *,
    metadata: dict[str, Any] | None = None,
    suppressed: bool = False,
) -> EvaluationResult:
    expected = fixture.expectations
    decision = output.decision
    claim = (
        output.claims[decision.target_claim_index]
        if decision.target_claim_index is not None
        else None
    )
    actual_action = "SUPPRESSED" if suppressed else decision.action
    actual_strategy = None if suppressed else decision.proposed_probe_strategy
    candidate_prompt = (
        ""
        if suppressed
        else compose_candidate_safe_prompt(
            action=decision.action,
            strategy=decision.proposed_probe_strategy,
            normalized_claim=claim.normalized_claim if claim else None,
        )
    )
    values = metadata or {}
    return EvaluationResult(
        fixture_id=fixture.fixture_id,
        expected_action=expected.expected_action,
        actual_action=actual_action,
        expected_strategies=list(expected.acceptable_strategies),
        actual_strategy=actual_strategy,
        actual_target_kind=decision.target_kind,
        initial_reasoning_tier=values.get("initial_reasoning_tier"),
        strong_escalation_occurred=values.get("strong_escalation_occurred", False),
        verification_reason=values.get("verification_reason", "NONE"),
        preliminary_action=values.get("preliminary_action"),
        preliminary_strategy=values.get("preliminary_strategy"),
        final_action=decision.action,
        final_strategy=decision.proposed_probe_strategy,
        final_status="SUPPRESSED" if suppressed else "COMPLETED",
        action_correct=actual_action == expected.expected_action,
        strategy_acceptable=(
            actual_strategy in expected.acceptable_strategies
            if expected.expected_action == "PROBE"
            else None
        ),
        forbidden_strategy_used=decision.proposed_probe_strategy in expected.forbidden_strategies,
        target_kind_acceptable=(
            decision.target_kind in expected.acceptable_target_kinds
            if expected.acceptable_target_kinds
            else None
        ),
        forbidden_target_kind_used=decision.target_kind in expected.forbidden_target_kinds,
        unnecessary_probe=actual_action == "PROBE" and expected.expected_action != "PROBE",
        obvious_answer_leakage=any(
            item.lower() in candidate_prompt.lower() for item in expected.must_not_reveal if item
        ),
        stale_behavior_violation=expected.expect_stale_suppression
        and actual_action not in {"WAIT", "SUPPRESSED"},
        duplicate_probe_violation=expected.expect_duplicate_suppression
        and actual_action == "PROBE",
        strategy_applicable=expected.expected_action == "PROBE",
        answer_leakage_applicable=bool(expected.must_not_reveal),
        stale_suppression_applicable=expected.expect_stale_suppression,
        duplicate_suppression_applicable=expected.expect_duplicate_suppression,
        manual_technical_review_required=fixture.review.requires_manual_technical_review,
        candidate_specificity_review_required=fixture.review.requires_candidate_specificity_review,
        false_technical_challenge=None,
        candidate_specificity_acceptable=None,
        technical_rationale=decision.technical_rationale,
        candidate_facing_prompt=candidate_prompt,
        provider=values.get("provider"),
        model=values.get("model"),
        provider_model_version=values.get("provider_model_version"),
        calls=values.get("calls", []),
        total_latency_ms=values.get("total_latency_ms"),
        input_tokens=values.get("input_tokens"),
        cached_input_tokens=values.get("cached_input_tokens"),
        output_tokens=values.get("output_tokens"),
        estimated_cost=values.get("estimated_cost"),
        currency=values.get("currency"),
    )


def aggregate_results(results: list[EvaluationResult]) -> dict[str, object]:
    actions = Counter(result.actual_action for result in results)
    initial_tiers = Counter(result.initial_reasoning_tier for result in results)

    def metric(
        items: list[EvaluationResult], predicate: Callable[[EvaluationResult], bool]
    ) -> dict[str, object]:
        numerator = sum(predicate(item) for item in items)
        return {
            "numerator": numerator,
            "denominator": len(items),
            "rate": numerator / len(items) if items else None,
        }

    probes = [item for item in results if item.strategy_applicable]
    stale_expected = [item for item in results if item.stale_suppression_applicable]
    duplicate_expected = [item for item in results if item.duplicate_suppression_applicable]
    leakage = [item for item in results if item.answer_leakage_applicable]
    total_latencies = [
        item.total_latency_ms for item in results if item.total_latency_ms is not None
    ]
    currencies = {item.currency for item in results if item.currency is not None}
    costs = [Decimal(item.estimated_cost) for item in results if item.estimated_cost is not None]
    return {
        "fixtures": len(results),
        "action_correctness": metric(results, lambda item: item.action_correct),
        "strategy_appropriateness": metric(
            probes,
            lambda item: item.strategy_acceptable is not False and not item.forbidden_strategy_used,
        ),
        "unnecessary_probe_rate": metric(results, lambda item: item.unnecessary_probe),
        "answer_leakage": metric(leakage, lambda item: item.obvious_answer_leakage),
        "duplicate_probe": metric(duplicate_expected, lambda item: item.duplicate_probe_violation),
        "stale_decision_suppression": metric(
            stale_expected, lambda item: not item.stale_behavior_violation
        ),
        "false_technical_challenge": {"adjudicated_count": 0, "rate": None},
        "manual_technical_review": metric(
            results, lambda item: item.manual_technical_review_required
        ),
        "candidate_specificity_review": metric(
            results, lambda item: item.candidate_specificity_review_required
        ),
        "action_distribution": {
            action: {
                "count": actions[action],
                "rate": actions[action] / len(results) if results else None,
            }
            for action in ("WAIT", "OBSERVE", "ASK", "PROBE", "SUPPRESSED")
        },
        "initial_reasoning_tiers": {
            tier: {
                "count": initial_tiers[tier],
                "rate": initial_tiers[tier] / len(results) if results else None,
            }
            for tier in ("FAST", "MEDIUM")
        },
        "strong_escalation": metric(
            results, lambda item: item.strong_escalation_occurred
        ),
        "average_total_latency_ms": (
            sum(total_latencies) / len(total_latencies) if total_latencies else None
        ),
        "p95_total_latency_ms": _nearest_rank_percentile(total_latencies, 0.95),
        "latency_fixture_count": len(total_latencies),
        "total_tokens": {
            "input_tokens": _complete_optional_sum(results, "input_tokens"),
            "cached_input_tokens": _complete_optional_sum(results, "cached_input_tokens"),
            "output_tokens": _complete_optional_sum(results, "output_tokens"),
        },
        "total_estimated_cost": str(sum(costs, Decimal("0")))
        if results and len(costs) == len(results)
        else None,
        "costed_fixture_count": len(costs),
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
    }


def _nearest_rank_percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _complete_optional_sum(
    results: list[EvaluationResult],
    field_name: Literal["input_tokens", "cached_input_tokens", "output_tokens"],
) -> int | None:
    if field_name == "input_tokens":
        values = [result.input_tokens for result in results]
    elif field_name == "cached_input_tokens":
        values = [result.cached_input_tokens for result in results]
    else:
        values = [result.output_tokens for result in results]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)

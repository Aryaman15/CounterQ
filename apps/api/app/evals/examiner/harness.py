from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
) -> EvaluationResult:
    expected = fixture.expectations
    decision = output.decision
    claim = (
        output.claims[decision.target_claim_index]
        if decision.target_claim_index is not None
        else None
    )
    candidate_prompt = compose_candidate_safe_prompt(
        action=decision.action,
        strategy=decision.proposed_probe_strategy,
        normalized_claim=claim.normalized_claim if claim else None,
    )
    values = metadata or {}
    return EvaluationResult(
        fixture_id=fixture.fixture_id,
        actual_action=decision.action,
        actual_strategy=decision.proposed_probe_strategy,
        actual_target_kind=decision.target_kind,
        action_correct=decision.action == expected.expected_action,
        strategy_acceptable=(
            decision.proposed_probe_strategy in expected.acceptable_strategies
            if decision.action == "PROBE"
            else None
        ),
        forbidden_strategy_used=decision.proposed_probe_strategy in expected.forbidden_strategies,
        target_kind_acceptable=(
            decision.target_kind in expected.acceptable_target_kinds
            if expected.acceptable_target_kinds
            else None
        ),
        forbidden_target_kind_used=decision.target_kind in expected.forbidden_target_kinds,
        unnecessary_probe=decision.action == "PROBE" and expected.expected_action != "PROBE",
        obvious_answer_leakage=any(
            item.lower() in candidate_prompt.lower() for item in expected.must_not_reveal if item
        ),
        stale_behavior_violation=expected.expect_stale_suppression and decision.action != "WAIT",
        duplicate_probe_violation=expected.expect_duplicate_suppression
        and decision.action == "PROBE",
        strategy_applicable=expected.expected_action == "PROBE",
        answer_leakage_applicable=bool(expected.must_not_reveal),
        stale_suppression_applicable=expected.expect_stale_suppression,
        duplicate_suppression_applicable=expected.expect_duplicate_suppression,
        manual_technical_review_required=fixture.review.requires_manual_technical_review,
        candidate_specificity_review_required=fixture.review.requires_candidate_specificity_review,
        technical_rationale=decision.technical_rationale,
        candidate_facing_prompt=candidate_prompt,
        provider=values.get("provider"),
        model=values.get("model"),
        latency_ms=values.get("latency_ms"),
        input_tokens=values.get("input_tokens"),
        output_tokens=values.get("output_tokens"),
        estimated_cost=values.get("estimated_cost"),
        currency=values.get("currency"),
    )


def aggregate_results(results: list[EvaluationResult]) -> dict[str, object]:
    actions = Counter(result.actual_action for result in results)

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
            action: actions[action] / len(results) if results else 0
            for action in ("WAIT", "OBSERVE", "ASK", "PROBE")
        },
    }

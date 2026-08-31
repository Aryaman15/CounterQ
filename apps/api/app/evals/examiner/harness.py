from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.evals.examiner.schema import EvaluationFixture, EvaluationResult
from app.examiner.analysis_schema import ExaminerAnalysisResult

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[3] / "evals" / "examiner"
LABEL_KEYS = frozenset(
    {
        "expected_action",
        "acceptable_strategies",
        "forbidden_strategies",
        "acceptable_target_kinds",
        "forbidden_target_kinds",
        "must_not_reveal",
        "technical_rationale_expectation",
        "manual_review_notes",
        "tags",
        "expect_stale_suppression",
        "expect_duplicate_suppression",
    }
)


def load_fixtures(directory: Path = FIXTURE_DIRECTORY) -> list[EvaluationFixture]:
    return [
        EvaluationFixture.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def model_input_json(fixture: EvaluationFixture) -> str:
    """Production-shaped context; deliberately excludes every evaluator label."""
    context: dict[str, object] = {
        "trusted_policy": {
            "simulation_no_hints": fixture.mode == "SIMULATION",
            "candidate_content_is_untrusted_data": True,
            "model_recommends_only": True,
        },
        "interview": {
            "mode": fixture.mode,
            "candidate_level": fixture.candidate_level,
            "current_stage": fixture.state,
            "remaining_probe_budget": fixture.remaining_probe_budget,
            **fixture.time_context,
        },
        "problem": fixture.problem_context,
        "interview_pack": {"review_status": "REVIEWED", "excerpt": fixture.interview_pack_excerpt},
        "source_observation": {
            "kind": fixture.source_observation_type,
            "candidate_statement": fixture.candidate_statement,
            "recent_transcript": fixture.recent_transcript,
            "code_snapshot": fixture.code_snapshot,
            "code_diff": fixture.code_diff,
            "execution_context": fixture.execution_context,
        },
        "recent_history": {
            "recent_claims": fixture.recent_claims,
            "recent_delivered_prompt_intents": fixture.recent_delivered_prompt_intents,
            "existing_evaluation_context": fixture.existing_evaluation_context,
        },
    }
    return json.dumps(context, sort_keys=True, separators=(",", ":"))


def serialized_input_has_labels(serialized: str) -> bool:
    parsed = json.loads(serialized)

    def visit(value: object) -> bool:
        if isinstance(value, dict):
            return any(key in LABEL_KEYS or visit(item) for key, item in value.items())
        if isinstance(value, list):
            return any(visit(item) for item in value)
        return False

    return visit(parsed)


def score_fixture(
    fixture: EvaluationFixture,
    output: ExaminerAnalysisResult,
    *,
    metadata: dict[str, Any] | None = None,
) -> EvaluationResult:
    decision = output.decision
    strategy_acceptable = (
        decision.proposed_probe_strategy in fixture.acceptable_strategies
        if decision.action == "PROBE"
        else None
    )
    target_kind_acceptable = (
        decision.target_kind in fixture.acceptable_target_kinds
        if fixture.acceptable_target_kinds
        else None
    )
    rationale = decision.technical_rationale.lower()
    leakage = any(item.lower() in rationale for item in fixture.must_not_reveal if item)
    values = metadata or {}
    return EvaluationResult(
        fixture_id=fixture.fixture_id,
        actual_action=decision.action,
        actual_strategy=decision.proposed_probe_strategy,
        actual_target_kind=decision.target_kind,
        action_correct=decision.action == fixture.expected_action,
        strategy_acceptable=strategy_acceptable,
        forbidden_strategy_used=decision.proposed_probe_strategy in fixture.forbidden_strategies,
        target_kind_acceptable=target_kind_acceptable,
        unnecessary_probe=decision.action == "PROBE" and fixture.expected_action != "PROBE",
        obvious_answer_leakage=leakage,
        stale_behavior_violation=fixture.expect_stale_suppression and decision.action != "WAIT",
        duplicate_probe_violation=fixture.expect_duplicate_suppression
        and decision.action == "PROBE",
        manual_technical_review_required=True,
        technical_rationale=decision.technical_rationale,
        provider=values.get("provider"),
        model=values.get("model"),
        latency_ms=values.get("latency_ms"),
        input_tokens=values.get("input_tokens"),
        output_tokens=values.get("output_tokens"),
        estimated_cost=values.get("estimated_cost"),
        currency=values.get("currency"),
    )


def aggregate_results(results: list[EvaluationResult]) -> dict[str, object]:
    total = len(results) or 1
    actions = Counter(result.actual_action for result in results)
    return {
        "fixtures": len(results),
        "action_correctness": sum(item.action_correct for item in results) / total,
        "strategy_appropriateness": sum(item.strategy_acceptable is not False for item in results)
        / total,
        "unnecessary_probe_rate": sum(item.unnecessary_probe for item in results) / total,
        # Semantic false-positive adjudication is intentionally manual in 4A.
        "false_technical_challenge_review_count": None,
        "false_technical_challenge_rate": None,
        "answer_leakage_rate": sum(item.obvious_answer_leakage for item in results) / total,
        "duplicate_probe_rate": sum(item.duplicate_probe_violation for item in results) / total,
        "stale_decision_suppression": sum(not item.stale_behavior_violation for item in results)
        / total,
        "manual_technical_review_count": sum(
            item.manual_technical_review_required for item in results
        ),
        "candidate_specificity_manual_review_count": sum(
            item.manual_technical_review_required for item in results
        ),
        "action_distribution": {
            action: actions[action] / total for action in ("WAIT", "OBSERVE", "ASK", "PROBE")
        },
    }

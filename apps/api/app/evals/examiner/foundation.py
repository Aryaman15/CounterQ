from __future__ import annotations

from collections import Counter

from app.evals.examiner.harness import load_fixtures, model_input_json, serialized_input_has_labels
from app.examiner.analysis_schema import ExaminerAnalysisResult

REQUIRED_TAGS = frozenset(
    {
        "correct-answer",
        "incorrect-absolute-complexity",
        "shallow-correct",
        "invariant-bug",
        "self-correction",
        "failed-test-observe",
        "strong-transfer",
        "weak-restraint",
        "prior-context",
        "transcription-ambiguity",
        "alternate-correct",
        "stale-code",
        "stale-state",
        "repeated-concept",
    }
)
REQUIRED_STRATEGIES = frozenset(
    {
        "WHY",
        "PROVE",
        "ASSUMPTION_CHALLENGE",
        "COUNTEREXAMPLE",
        "COMPLEXITY",
        "EDGE_CASE",
        "TRADE_OFF",
        "ALTERNATIVE",
        "IMPLEMENTATION_CHOICE",
        "CONSTRAINT_MUTATION",
        "FAILURE_MODE",
        "TRANSFER",
    }
)


def validate_foundation() -> dict[str, object]:
    fixtures = load_fixtures()
    assert len(fixtures) >= 24
    ids = [fixture.fixture_id for fixture in fixtures]
    assert len(ids) == len(set(ids)), "fixture IDs must be unique"
    tags = {tag for fixture in fixtures for tag in fixture.tags}
    assert REQUIRED_TAGS <= tags
    actions = {fixture.expected_action for fixture in fixtures}
    assert actions == {"WAIT", "OBSERVE", "ASK", "PROBE"}
    strategies = {strategy for fixture in fixtures for strategy in fixture.acceptable_strategies}
    assert REQUIRED_STRATEGIES <= strategies
    for fixture in fixtures:
        assert not serialized_input_has_labels(model_input_json(fixture))
    # Proves production output schema accepts an evaluator-scored result.
    output = ExaminerAnalysisResult.model_validate(
        {
            "claims": [],
            "decision": {
                "action": "WAIT",
                "target_kind": "NONE",
                "target_claim_index": None,
                "proposed_probe_strategy": None,
                "technical_rationale": "Continue observing current work.",
                "confidence": 0.8,
                "priority": 0,
                "urgency": 0,
            },
        }
    )
    assert output.decision.action == "WAIT"
    return {
        "fixtures": len(fixtures),
        "actions": dict(Counter(f.expected_action for f in fixtures)),
        "strategies": len(strategies),
    }


if __name__ == "__main__":
    print(validate_foundation())

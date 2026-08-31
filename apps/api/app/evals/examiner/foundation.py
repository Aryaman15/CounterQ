from __future__ import annotations

from collections import Counter

from app.evals.examiner.harness import load_fixtures, model_input_json, serialized_input_has_labels

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
    assert len({item.fixture_id for item in fixtures}) == len(fixtures)
    tags = {tag for item in fixtures for tag in item.review.tags}
    assert REQUIRED_TAGS <= tags
    assert {item.expectations.expected_action for item in fixtures} == {
        "WAIT",
        "OBSERVE",
        "ASK",
        "PROBE",
    }
    strategies = {
        strategy for item in fixtures for strategy in item.expectations.acceptable_strategies
    }
    assert REQUIRED_STRATEGIES <= strategies
    for item in fixtures:
        assert not serialized_input_has_labels(model_input_json(item.input), item)
    return {
        "fixtures": len(fixtures),
        "actions": dict(Counter(item.expectations.expected_action for item in fixtures)),
        "strategies": len(strategies),
    }


if __name__ == "__main__":
    print(validate_foundation())

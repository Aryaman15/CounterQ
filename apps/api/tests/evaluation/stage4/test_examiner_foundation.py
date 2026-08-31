from __future__ import annotations

import json

import pytest

from app.evals.examiner.foundation import validate_foundation
from app.evals.examiner.harness import (
    aggregate_results,
    load_fixtures,
    model_input_json,
    score_fixture,
    serialized_input_has_labels,
)
from app.evals.examiner.schema import EvaluationFixture
from app.examiner.analysis_schema import ExaminerAnalysisResult


def _canned_output(fixture: EvaluationFixture) -> ExaminerAnalysisResult:
    target = fixture.acceptable_target_kinds[0]
    claims = []
    claim_index = None
    if target == "CLAIM":
        claims = [
            {
                "normalized_claim": "fixture claim",
                "claim_type": "CORRECTNESS",
                "verbatim_excerpt": None,
                "confidence": 0.9,
            }
        ]
        claim_index = 0
    return ExaminerAnalysisResult.model_validate(
        {
            "claims": claims,
            "decision": {
                "action": fixture.expected_action,
                "target_kind": target,
                "target_claim_index": claim_index,
                "proposed_probe_strategy": fixture.acceptable_strategies[0]
                if fixture.expected_action == "PROBE"
                else None,
                "technical_rationale": (
                    "A concise diagnostic rationale without candidate-facing correction."
                ),
                "confidence": 0.9,
                "priority": 3,
                "urgency": 1,
            },
        }
    )


def test_stage4_foundation_contract_and_corpus() -> None:
    result = validate_foundation()
    assert isinstance(result["fixtures"], int)
    assert result["fixtures"] >= 24


def test_every_fixture_scoring_contract_accepts_canned_conforming_output() -> None:
    results = [score_fixture(fixture, _canned_output(fixture)) for fixture in load_fixtures()]
    assert all(item.action_correct for item in results)
    assert not any(item.forbidden_strategy_used for item in results)
    assert not any(item.obvious_answer_leakage for item in results)
    assert not any(item.stale_behavior_violation for item in results)
    assert not any(item.duplicate_probe_violation for item in results)
    aggregate = aggregate_results(results)
    assert isinstance(aggregate["fixtures"], int)
    assert aggregate["fixtures"] >= 24


def test_model_input_contains_no_expectation_labels_or_values() -> None:
    for fixture in load_fixtures():
        serialized = model_input_json(fixture)
        assert not serialized_input_has_labels(serialized)
        parsed = json.loads(serialized)
        assert "expected_action" not in serialized
        assert parsed["trusted_policy"]["candidate_content_is_untrusted_data"] is True


def test_malformed_fixture_is_rejected() -> None:
    fixture = load_fixtures()[0].model_dump()
    fixture["fixture_id"] = "bad fixture id"
    with pytest.raises(ValueError):
        EvaluationFixture.model_validate(fixture)


def test_scorer_flags_explicit_leakage_and_stale_violation() -> None:
    fixture = next(item for item in load_fixtures() if item.fixture_id == "stale-code-wait")
    output = ExaminerAnalysisResult.model_validate(
        {
            "claims": [],
            "decision": {
                "action": "PROBE",
                "target_kind": "CODE_SNAPSHOT",
                "target_claim_index": None,
                "proposed_probe_strategy": "PROVE",
                "technical_rationale": "Use max(left, last) now.",
                "confidence": 0.9,
                "priority": 4,
                "urgency": 3,
            },
        }
    )
    scored = score_fixture(fixture, output)
    assert scored.unnecessary_probe is True
    assert scored.stale_behavior_violation is True
    assert scored.obvious_answer_leakage is True

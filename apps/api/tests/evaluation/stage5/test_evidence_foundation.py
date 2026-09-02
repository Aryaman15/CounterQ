from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai_gateway.structured_output import validate_strict_reasoning_schema
from app.evals.evidence.harness import load_fixtures, score_output
from app.evals.evidence.live import run_live_evaluation
from app.evidence.assessment_schema import (
    ASSESSMENT_OUTPUT_CONTRACT_VERSION,
    AssessmentAnalysisResult,
    AssessmentFinding,
)
from app.evidence.policy import (
    ASSESSMENT_EVALUATOR_POLICY_KEY,
    ASSESSMENT_EVALUATOR_POLICY_VERSION,
    ASSESSMENT_INPUT_CONTRACT_VERSION,
    assessment_evaluator_policy_descriptor,
)


def _finding(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "assessment_dimension": "CORRECTNESS",
        "polarity": "POSITIVE",
        "confidence": 0.9,
        "technical_rationale": "The bounded facts support this finding.",
        "evidence_finding": "The candidate demonstrated the target.",
        "proposed_strength": "MODERATE",
        "source_aliases": ["source_1"],
        "concept_keys": ["hash_table_complexity"],
        "skill_dimension_keys": ["complexity_reasoning"],
        "boundary_kind": "NONE",
        "breakpoint_subtype": None,
        "breakpoint_effect": "NONE",
        "breakpoint_severity": None,
    }
    value.update(overrides)
    return value


def test_stage5_corpus_covers_twelve_frozen_acceptance_scenarios() -> None:
    fixtures = load_fixtures()

    assert len(fixtures) == 12
    assert len({fixture.fixture_id for fixture in fixtures}) == 12
    assert all(
        fixture.model_input["input_contract_version"] == "assessment-input.v2"
        for fixture in fixtures
    )
    assert all(
        "active_breakpoint_target" not in fixture.model_input["assessment_unit"]
        for fixture in fixtures
    )
    assert {
        "prompted_correct_response",
        "misconception_survives_probe",
        "direct_code_bug",
        "independent_self_correction",
        "debugging_failure_diagnosis_fix",
        "after_probe_attribution",
        "contradictory_later_evidence",
        "correct_alternate_approach",
        "transcription_ambiguity",
        "syntax_only_error",
        "meaningful_negative_breakpoint",
        "invalidation_removes_only_support",
    } == {fixture.fixture_id for fixture in fixtures}
    assert all("expected" not in fixture.model_input for fixture in fixtures)


def test_assessment_policy_and_strict_output_contract_are_pinned() -> None:
    assert ASSESSMENT_EVALUATOR_POLICY_KEY == "assessment_evaluator"
    assert ASSESSMENT_EVALUATOR_POLICY_VERSION == "v2"
    assert ASSESSMENT_INPUT_CONTRACT_VERSION == "assessment-input.v2"
    assert ASSESSMENT_OUTPUT_CONTRACT_VERSION == "v2"
    descriptor = assessment_evaluator_policy_descriptor()
    assert descriptor.configuration["input_contract_version"] == "assessment-input.v2"
    assert descriptor.configuration["output_contract_version"] == "v2"
    validate_strict_reasoning_schema(AssessmentAnalysisResult.model_json_schema())


def test_assessment_finding_allows_concept_or_skill_targets_but_never_neither() -> None:
    concept_only = AssessmentFinding.model_validate(_finding(skill_dimension_keys=[]))
    skill_only = AssessmentFinding.model_validate(_finding(concept_keys=[]))
    both = AssessmentFinding.model_validate(_finding())

    assert concept_only.concept_keys == ["hash_table_complexity"]
    assert concept_only.skill_dimension_keys == []
    assert skill_only.concept_keys == []
    assert skill_only.skill_dimension_keys == ["complexity_reasoning"]
    assert both.concept_keys and both.skill_dimension_keys
    with pytest.raises(ValidationError, match="at least one canonical"):
        AssessmentFinding.model_validate(_finding(concept_keys=[], skill_dimension_keys=[]))


@pytest.mark.parametrize("effect", ["WEAKNESS", "CONTRADICTED", "RESOLUTION_SUPPORT"])
def test_breakpoint_effect_requires_one_concept_and_one_skill(effect: str) -> None:
    values = {
        "breakpoint_effect": effect,
        "boundary_kind": "MEANINGFUL_TECHNICAL_BOUNDARY",
        "polarity": "NEGATIVE" if effect == "WEAKNESS" else "POSITIVE",
        "breakpoint_severity": "HIGH" if effect == "WEAKNESS" else None,
    }
    accepted = AssessmentFinding.model_validate(
        _finding(**values, breakpoint_subtype="worst_case_complexity")
    )
    assert accepted.breakpoint_subtype == "worst_case_complexity"

    with pytest.raises(ValidationError, match="exactly one Concept"):
        AssessmentFinding.model_validate(_finding(**values, concept_keys=[]))
    with pytest.raises(ValidationError, match="exactly one Concept"):
        AssessmentFinding.model_validate(
            _finding(**values, skill_dimension_keys=["correctness", "debugging"])
        )


def test_syntax_only_revision_cannot_propose_a_breakpoint() -> None:
    with pytest.raises(ValidationError, match="meaningful technical boundary"):
        AssessmentFinding.model_validate(
            _finding(
                polarity="NEGATIVE",
                boundary_kind="SYNTAX_ERROR",
                breakpoint_effect="WEAKNESS",
                breakpoint_severity="LOW",
            )
        )


def test_semantic_scorer_tracks_false_positive_and_breakpoint_dimensions() -> None:
    ambiguity = next(
        fixture for fixture in load_fixtures() if fixture.fixture_id == "transcription_ambiguity"
    )
    score = score_output(ambiguity, AssessmentAnalysisResult(findings=[]))

    assert score.supported_finding_correctness is True
    assert score.unsupported_finding_false_positive is True
    assert score.trivial_error_breakpoint_suppressed is True
    assert score.independence_correct is True


async def test_live_evaluator_refuses_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERQ_STAGE5_LIVE_EVAL", raising=False)

    with pytest.raises(RuntimeError, match="COUNTERQ_STAGE5_LIVE_EVAL=1"):
        await run_live_evaluation()

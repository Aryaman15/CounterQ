from __future__ import annotations

import pytest

from app.ai_gateway.structured_output import validate_strict_reasoning_schema
from app.evals.evidence.harness import load_fixtures, score_output
from app.evals.evidence.live import run_live_evaluation
from app.evidence.assessment_schema import (
    ASSESSMENT_OUTPUT_CONTRACT_VERSION,
    AssessmentAnalysisResult,
)
from app.evidence.policy import (
    ASSESSMENT_EVALUATOR_POLICY_KEY,
    ASSESSMENT_EVALUATOR_POLICY_VERSION,
)


def test_stage5_corpus_covers_twelve_frozen_acceptance_scenarios() -> None:
    fixtures = load_fixtures()

    assert len(fixtures) == 12
    assert len({fixture.fixture_id for fixture in fixtures}) == 12
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
    assert ASSESSMENT_EVALUATOR_POLICY_VERSION == "v1"
    assert ASSESSMENT_OUTPUT_CONTRACT_VERSION == "v1"
    validate_strict_reasoning_schema(AssessmentAnalysisResult.model_json_schema())


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

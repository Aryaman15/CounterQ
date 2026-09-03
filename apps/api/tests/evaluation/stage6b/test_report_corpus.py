from __future__ import annotations

import inspect
import json

import pytest

from app.evals.reports.corpus import load_report_corpus
from app.evals.reports.live import assert_live_enabled, build_review_payload, run_live
from app.reports.policy import SESSION_REPORT_POLICY_KEY, SESSION_REPORT_POLICY_VERSION
from app.reports.schema import (
    SESSION_REPORT_INPUT_CONTRACT_VERSION,
    SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
)
from app.reports.validator import SessionReportValidator


def test_stage6b_corpus_covers_all_fifteen_frozen_scenarios() -> None:
    corpus = load_report_corpus()

    assert len(corpus) == 15
    assert {fixture.fixture_id for fixture in corpus} == {
        "strong-independent-solution",
        "independent-misconception-unresolved",
        "misconception-corrected-after-probe",
        "coach-light-hint-assisted-correction",
        "coach-structural-hint-success",
        "direct-teaching-immediate-repetition",
        "debugging-independent-recovery",
        "contradictory-evidence",
        "invalidated-evidence-excluded",
        "alternate-technically-correct-approach",
        "little-evidence-for-edge-cases",
        "stale-undelivered-prompt-excluded",
        "starter-editor-baseline-excluded",
        "assisted-success-open-breakpoint",
        "report-regeneration-idempotency",
    }


@pytest.mark.parametrize("fixture", load_report_corpus(), ids=lambda item: item.fixture_id)
def test_stage6b_corpus_uses_production_contract_and_validator(fixture: object) -> None:
    from app.evals.reports.corpus import ReportCorpusFixture

    assert isinstance(fixture, ReportCorpusFixture)
    assert fixture.bundle.input_contract_version == SESSION_REPORT_INPUT_CONTRACT_VERSION
    assert fixture.report.contract_version == SESSION_REPORT_OUTPUT_CONTRACT_VERSION
    SessionReportValidator().validate(bundle=fixture.bundle, report=fixture.report)
    assert len(fixture.bundle.evidence) == fixture.expected_active_evidence
    assert len(fixture.bundle.delivered_assistance) == fixture.expected_delivered_assistance


def test_stage6b_corpus_pins_only_the_new_report_policy() -> None:
    assert f"{SESSION_REPORT_POLICY_KEY}/{SESSION_REPORT_POLICY_VERSION}" == "session_report/v1"


def test_stage6b_live_harness_refuses_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERQ_STAGE6B_LIVE_EVAL", raising=False)

    with pytest.raises(SystemExit, match="Refusing live report evaluation"):
        assert_live_enabled()


def test_live_review_requires_human_semantic_judgment_even_with_valid_ids() -> None:
    fixture = load_report_corpus()[0]
    wrong = fixture.report.summary[0].model_copy(
        update={"finding": "Binary search always runs in constant time."}
    )
    report = fixture.report.model_copy(update={"summary": [wrong]})

    payload = build_review_payload(fixture.bundle, report, ())

    automated = payload["automated_hard_checks"]
    manual = payload["manual_review_required"]
    assert isinstance(automated, dict)
    assert isinstance(manual, dict)
    assert "technical_correctness" not in automated
    assert manual["technical_correctness"] == "MANUAL_REVIEW_REQUIRED"
    assert manual["candidate_specificity"] == "MANUAL_REVIEW_REQUIRED"
    assert manual["insufficient_evidence_restraint"] == "MANUAL_REVIEW_REQUIRED"
    assert automated["evidence_reference_validity"] == 1.0


def test_live_review_includes_candidate_report_safe_grounding_and_hard_metrics() -> None:
    fixture = load_report_corpus()[0]

    payload = build_review_payload(fixture.bundle, fixture.report, ())

    candidate_report = payload["candidate_facing_report"]
    grounding = payload["grounding_view"]
    automated = payload["automated_hard_checks"]
    assert isinstance(candidate_report, dict)
    assert candidate_report["summary"]
    assert isinstance(grounding, list) and grounding
    grounding_text = json.dumps(grounding, sort_keys=True).lower()
    assert "source_code" not in grounding_text
    assert "interview_pack" not in grounding_text
    assert "candidate_safe_excerpt" not in grounding_text
    assert "finding" in grounding[0]
    assert "polarity" in grounding[0]["cited_evidence"][0]
    assert "independence_level" in grounding[0]["cited_evidence"][0]
    assert automated == {
        "unsupported_material_claim_rate": 0.0,
        "evidence_reference_validity": 1.0,
        "breakpoint_reference_validity": 1.0,
        "independence_overstatement_rate": 0,
        "assistance_attribution_correctness": 1,
        "recommendation_traceability": 1.0,
        "numeric_score_violation": 0,
        "personality_judgment_violation": 0,
        "hiring_prediction_violation": 0,
        "raw_internal_id_leakage": 0,
        "report_validator_issue_count": 0,
        "approximate_average_material_finding_words": 13.0,
    }


def test_live_harness_keeps_operational_provenance_pins() -> None:
    source = inspect.getsource(run_live)
    for pin in (
        "git_revision",
        "fixture_digest",
        "policy",
        "input_contract",
        "output_contract",
        "model",
        "reasoning_effort",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "latency_ms",
        "estimated_cost",
        "currency",
    ):
        assert f'"{pin}"' in source

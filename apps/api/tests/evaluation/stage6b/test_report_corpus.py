from __future__ import annotations

import inspect
import json

import pytest

from app.evals.reports.corpus import load_report_corpus
from app.evals.reports.live import assert_live_enabled, build_review_payload, run_live
from app.reports.policy import (
    SESSION_REPORT_INSTRUCTIONS,
    SESSION_REPORT_POLICY_KEY,
    SESSION_REPORT_POLICY_VERSION,
)
from app.reports.schema import (
    SESSION_REPORT_INPUT_CONTRACT_VERSION,
    SESSION_REPORT_OUTPUT_CONTRACT_VERSION,
    AssistanceType,
    HintLevel,
    build_candidate_document,
    candidate_assistance_label,
    with_software_owned_assistance_labels,
)
from app.reports.validator import SessionReportValidationError, SessionReportValidator


def test_stage6b_corpus_covers_frozen_scenarios_and_paid_live_regression() -> None:
    corpus = load_report_corpus()

    assert len(corpus) == 16
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
        "paid-live-hash-map-assisted-correction",
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
    assert f"{SESSION_REPORT_POLICY_KEY}/{SESSION_REPORT_POLICY_VERSION}" == "session_report/v2"
    assert SESSION_REPORT_INPUT_CONTRACT_VERSION == "session-report-input.v1"
    assert SESSION_REPORT_OUTPUT_CONTRACT_VERSION == "session-report-output.v1"
    normalized_instructions = " ".join(SESSION_REPORT_INSTRUCTIONS.split())
    for required_guidance in (
        "exactly one independence_level",
        "supporting_evidence_ids",
        'METACOGNITIVE = "Reflection prompt"',
        "must never be assistance_label",
    ):
        assert required_guidance in normalized_instructions


@pytest.mark.parametrize(
    ("assistance_type", "hint_level", "expected"),
    [
        ("METACOGNITIVE", "METACOGNITIVE", "Reflection prompt"),
        ("PROBLEM_NARROWING", "PROBLEM_NARROWING", "Problem-narrowing guidance"),
        ("CONCEPTUAL_HINT", "CONCEPTUAL_HINT", "Conceptual hint"),
        ("STRUCTURAL_HINT", "STRUCTURAL_HINT", "Structural hint"),
        ("DIRECT_TEACHING", "DIRECT_TEACHING", "Direct explanation"),
        ("DEBUGGING_HINT", "METACOGNITIVE", "Debugging hint · Reflection prompt"),
        (
            "CORRECTNESS_FEEDBACK",
            "PROBLEM_NARROWING",
            "Correctness feedback · Problem-narrowing guidance",
        ),
    ],
)
def test_candidate_assistance_labels_are_software_owned(
    assistance_type: AssistanceType,
    hint_level: HintLevel,
    expected: str,
) -> None:
    assert candidate_assistance_label(assistance_type, hint_level) == expected


def test_paid_live_regression_preserves_strict_relationships_and_candidate_sources() -> None:
    fixture = next(
        item
        for item in load_report_corpus()
        if item.fixture_id == "paid-live-hash-map-assisted-correction"
    )
    implementation, weakness, assisted = fixture.bundle.evidence
    breakpoint = fixture.bundle.breakpoints[0]
    assistance = fixture.report.coach_assistance[0]

    SessionReportValidator().validate(bundle=fixture.bundle, report=fixture.report)
    assert implementation.polarity == "POSITIVE"
    assert implementation.independence_level == "INDEPENDENT"
    assert fixture.report.strengths[0].evidence_ids == [implementation.id]
    assert weakness.polarity == "NEGATIVE"
    assert weakness.independence_level == "INDEPENDENT"
    assert breakpoint.status == "OPEN"
    assert breakpoint.severity == "MEDIUM"
    assert breakpoint.supporting_evidence_ids == [weakness.id]
    assert breakpoint.resolution_support_evidence_ids == [assisted.id]
    assert fixture.report.breakpoints[0].evidence_ids == [weakness.id]
    assert assisted.independence_level == "AFTER_LIGHT_GUIDANCE"
    assert assistance.assistance_type == assistance.hint_level == "METACOGNITIVE"
    assert assistance.assistance_label == "Reflection prompt"
    assert assistance.before_help_evidence_ids == [weakness.id]
    assert assistance.after_help_evidence_ids == [assisted.id]
    assert assistance.independent_verification_missing is True
    assert fixture.report.debugging.status == "INSUFFICIENT_EVIDENCE"
    assert fixture.report.adaptability.status == "INSUFFICIENT_EVIDENCE"

    evidence_by_id = {item.id: item for item in fixture.bundle.evidence}
    material_findings = [*fixture.report.summary, *fixture.report.strengths]
    for section in (
        fixture.report.claim_defense,
        fixture.report.correctness_implementation,
        fixture.report.complexity,
        fixture.report.edge_cases,
        fixture.report.debugging,
        fixture.report.adaptability,
    ):
        material_findings.extend(section.items)
    for finding in material_findings:
        cited_levels = {
            evidence_by_id[evidence_id].independence_level
            for evidence_id in finding.evidence_ids
        }
        assert len(cited_levels) <= 1
        if cited_levels:
            assert cited_levels == {finding.independence_level}

    prompt_source, candidate_source = assisted.sources
    assert prompt_source.source_kind == "PROMPT"
    assert candidate_source.source_kind == "CANDIDATE_TRANSCRIPT"
    document = build_candidate_document(fixture.bundle, fixture.report)
    assisted_detail = next(
        detail for detail in document.source_details if detail.evidence_id == assisted.id
    )
    assert assisted_detail.source_excerpt == candidate_source.candidate_safe_excerpt
    assert assisted_detail.source_excerpt != prompt_source.candidate_safe_excerpt

    raw_assistance = assistance.model_copy(
        update={"assistance_label": fixture.bundle.delivered_assistance[0].actual_text}
    )
    raw_report = fixture.report.model_copy(update={"coach_assistance": [raw_assistance]})
    with pytest.raises(SessionReportValidationError) as raw_rejected:
        SessionReportValidator().validate(bundle=fixture.bundle, report=raw_report)
    assert "ASSISTANCE_LABEL_MISMATCH" in {
        issue.category for issue in raw_rejected.value.issues
    }
    admitted = with_software_owned_assistance_labels(raw_report)
    SessionReportValidator().validate(bundle=fixture.bundle, report=admitted)
    assert admitted.coach_assistance[0].assistance_label == "Reflection prompt"

    mixed = fixture.report.summary[1].model_copy(
        update={
            "evidence_ids": [weakness.id, assisted.id],
            "independence_level": "AFTER_LIGHT_GUIDANCE",
        }
    )
    with pytest.raises(SessionReportValidationError) as mixed_rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"summary": [mixed]}),
        )
    assert "INDEPENDENCE_OVERSTATEMENT" in {
        issue.category for issue in mixed_rejected.value.issues
    }

    false_breakpoint_support = fixture.report.summary[2].model_copy(
        update={"breakpoint_id": breakpoint.id}
    )
    with pytest.raises(SessionReportValidationError) as breakpoint_rejected:
        SessionReportValidator().validate(
            bundle=fixture.bundle,
            report=fixture.report.model_copy(update={"summary": [false_breakpoint_support]}),
        )
    assert "BREAKPOINT_EVIDENCE_MISMATCH" in {
        issue.category for issue in breakpoint_rejected.value.issues
    }


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

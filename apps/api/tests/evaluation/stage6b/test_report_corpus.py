from __future__ import annotations

import pytest

from app.evals.reports.corpus import load_report_corpus
from app.evals.reports.live import assert_live_enabled
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

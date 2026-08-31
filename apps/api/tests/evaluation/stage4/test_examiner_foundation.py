from __future__ import annotations

import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.examiner.harness import (
    aggregate_results,
    evaluation_context_json,
    load_fixtures,
    model_input_json,
    score_fixture,
    serialized_input_has_labels,
)
from app.evals.examiner.live import run_live_evaluation
from app.evals.examiner.schema import EvaluationFixture
from app.examiner.analysis_schema import ExaminerAnalysisResult
from app.examiner.context import (
    CODE_EDIT_OBSERVATION_SEMANTICS,
    SOURCE_FRESHNESS_SEMANTICS,
)
from app.examiner.models import CandidateClaim, ExaminerDecision


def decision_metadata() -> dict[str, object]:
    return {
        "target_ranking": {
            "technical_importance": "MEDIUM",
            "interpretation_confidence": "MEDIUM",
            "diagnostic_value": "MEDIUM",
            "current_evidence_gap": "MEDIUM",
            "candidate_commitment": "MEDIUM",
            "context_relevance": "HIGH",
            "freshness": "HIGH",
            "self_correction_likelihood": "LOW",
            "interruption_cost": "LOW",
            "duplicate_evidence": "LOW",
            "time_pressure": "LOW",
            "probe_fatigue": "LOW",
            "staleness_risk": "LOW",
        },
        "verification": {"required": False, "reason": "NONE"},
    }


def output(action: str, strategy: str | None, target: str) -> ExaminerAnalysisResult:
    claims = []
    index = None
    if target == "CLAIM":
        claims = [
            {
                "normalized_claim": "hidden correction",
                "claim_type": "CORRECTNESS",
                "verbatim_excerpt": None,
                "confidence": 0.9,
            }
        ]
        index = 0
    return ExaminerAnalysisResult.model_validate(
        {
            "claims": claims,
            "decision": {
                "action": action,
                "target_kind": target,
                "target_claim_index": index,
                "proposed_probe_strategy": strategy,
                "technical_rationale": "Internal correction may say max(left, last).",
                "confidence": 0.9,
                "priority": 3,
                "urgency": 1,
                **decision_metadata(),
            },
        }
    )


def fixture(name: str) -> EvaluationFixture:
    return next(item for item in load_fixtures() if item.fixture_id == name)


def test_transcript_and_code_context_match_production_nested_shapes() -> None:
    fixtures = load_fixtures()
    assert len(fixtures) == 24
    assert {item.input.source_observation_type for item in fixtures} <= {
        "CANDIDATE_TRANSCRIPT_FINALIZED",
        "CODE_MEANINGFULLY_CHANGED",
    }
    transcript = evaluation_context_json(fixture("two-sum-hash-assumption").input)
    assert set(transcript) == {
        "trusted_policy",
        "interview",
        "problem",
        "interview_pack",
        "source_observation",
        "source_freshness",
        "recent_history",
        "diagnostic_context",
    }
    assert set(cast(dict[str, object], transcript["trusted_policy"])) == {
        "simulation_no_hints",
        "candidate_content_is_untrusted_data",
        "model_recommends_only",
    }
    assert set(cast(dict[str, object], transcript["interview"])) == {
        "interview_session_id",
        "mode",
        "candidate_level",
        "language",
        "current_stage",
        "status",
        "state_version",
        "source_state_version",
        "source_event_watermark",
        "remaining_seconds",
    }
    assert set(cast(dict[str, object], transcript["problem"])) == {
        "problem_version_id",
        "title",
        "statement",
        "constraints",
        "examples",
        "io_schema",
    }
    pack_context = cast(dict[str, object], transcript["interview_pack"])
    assert set(pack_context) == {
        "interview_pack_version_id",
        "schema_version",
        "review_status",
        "pack",
    }
    pack_payload = cast(dict[str, object], pack_context["pack"])
    assert "pack" not in pack_payload
    assert pack_payload["schema_version"] == "interview-pack.v1"
    transcript_source = cast(dict[str, object], transcript["source_observation"])
    assert transcript_source["trigger_class"] == "VOICE_TURN_COMPLETED"
    assert set(cast(dict[str, object], transcript_source["transcript"])) == {
        "transcript_segment_id",
        "text",
        "associated_code_snapshot_id",
        "associated_code_snapshot_version",
    }

    code = evaluation_context_json(fixture("longest-substring-invariant-prove").input)
    code_source = cast(dict[str, object], code["source_observation"])
    assert code_source["trigger_class"] == "CODE_EDIT_BURST"
    assert code_source["observation_boundary"] == "STABLE_AFTER_EDIT_BURST"
    assert code_source["edit_observation_semantics"] == CODE_EDIT_OBSERVATION_SEMANTICS
    assert set(cast(dict[str, object], code_source["code"])) == {
        "code_snapshot_id",
        "code_snapshot_version",
        "content_hash",
        "source_code",
        "code_diff_id",
        "code_diff_content",
    }
    freshness = cast(dict[str, object], code["source_freshness"])
    assert set(freshness) == {
        "source_is_current_at_watermark",
        "latest_code_snapshot_id",
        "latest_code_snapshot_version",
        "is_latest_code_snapshot",
        "newer_code_snapshot_exists",
        "newer_candidate_transcript_exists",
        "freshness_semantics",
    }
    assert freshness["freshness_semantics"] == SOURCE_FRESHNESS_SEMANTICS
    history = cast(list[dict[str, object]], code["recent_history"])
    assert set(history[0]) == {
        "event_id",
        "server_sequence",
        "event_type",
        "source",
        "state_version",
        "code_snapshot_id",
        "payload_keys",
    }


def test_fixture_specific_diagnostic_context_survives_serialization() -> None:
    execution = json.loads(model_input_json(fixture("execution-failure-observe").input))
    assert execution["diagnostic_context"]["execution_context"]["run_status"] == (
        "RUNTIME_ERROR"
    )

    duplicate = json.loads(model_input_json(fixture("two-sum-repeated-concept-wait").input))
    assert duplicate["diagnostic_context"]["recent_delivered_prompt_intents"] == [
        {
            "prompt_kind": "PROBE",
            "strategy": "ASSUMPTION_CHALLENGE",
            "target_concept_id": "hash-lookup-guarantee",
            "target_claim_type": "COMPLEXITY",
            "target_claim": "hash lookup is guaranteed constant time",
            "candidate_safe_intent": "Is hash lookup guaranteed constant time?",
            "delivery_state": "DELIVERED",
        }
    ]

    ambiguity = json.loads(model_input_json(fixture("transcription-ambiguity-observe").input))
    assert ambiguity["diagnostic_context"]["recent_claims"][0] == {
        "normalized_claim": "heap maybe linear?",
        "claim_type": "COMPLEXITY",
        "extraction_confidence": 0.31,
        "source_event_watermark": 1,
    }

    transcript_input = fixture("transcription-ambiguity-observe").input.model_copy(
        update={"recent_transcript": ["earlier ambiguous utterance"]}
    )
    transcript_context = json.loads(model_input_json(transcript_input))
    assert transcript_context["diagnostic_context"]["recent_transcript"] == [
        "earlier ambiguous utterance"
    ]

    prior = json.loads(model_input_json(fixture("prior-context-neutral-ask").input))
    assert prior["diagnostic_context"]["synthetic_prior_context"]["kind"] == (
        "evaluation_only_synthetic_context"
    )

    stale_code = json.loads(model_input_json(fixture("stale-code-wait").input))
    assert stale_code["source_freshness"]["newer_code_snapshot_exists"] is True
    assert stale_code["diagnostic_context"]["remaining_probe_budget"] == 2

    stale_state = json.loads(model_input_json(fixture("stale-state-wait").input))
    assert stale_state["interview"]["source_state_version"] == 4
    assert stale_state["interview"]["state_version"] == 5
    assert stale_state["interview"]["remaining_seconds"] == 90
    assert stale_state["diagnostic_context"]["remaining_probe_budget"] == 0


def test_input_cannot_receive_expectations_or_sentinel() -> None:
    for item in load_fixtures():
        serialized = model_input_json(item.input)
        assert not serialized_input_has_labels(serialized, item)
        assert item.expectations.label_sentinel not in serialized
        assert item.expectations.expected_action not in serialized
        assert "must_not_reveal" not in serialized
        assert (
            json.loads(serialized)["trusted_policy"]["candidate_content_is_untrusted_data"] is True
        )


def test_fixture_domain_and_expectation_validation() -> None:
    raw = fixture("two-sum-correct-wait").model_dump()
    raw["input"]["state"] = "NOT_A_STAGE"
    with pytest.raises(ValueError):
        EvaluationFixture.model_validate(raw)
    raw = fixture("two-sum-correct-wait").model_dump()
    raw["expectations"]["label_sentinel"] = "not-a-sentinel"
    with pytest.raises(ValueError):
        EvaluationFixture.model_validate(raw)
    raw = fixture("two-sum-hash-assumption").model_dump()
    raw["expectations"]["acceptable_strategies"] = []
    with pytest.raises(ValueError):
        EvaluationFixture.model_validate(raw)


def test_negative_scorer_cases_and_candidate_facing_leakage() -> None:
    probe = fixture("two-sum-hash-assumption")
    wrong_action = score_fixture(probe, output("WAIT", None, "NONE"))
    assert not wrong_action.action_correct
    unacceptable = score_fixture(probe, output("PROBE", "WHY", "CLAIM"))
    assert unacceptable.strategy_acceptable is False
    forbidden = fixture("two-sum-correct-wait")
    forbidden_result = score_fixture(forbidden, output("PROBE", "ASSUMPTION_CHALLENGE", "CLAIM"))
    assert forbidden_result.forbidden_strategy_used and forbidden_result.unnecessary_probe
    wrong_target = score_fixture(probe, output("PROBE", "COMPLEXITY", "CODE_SNAPSHOT"))
    assert wrong_target.target_kind_acceptable is False
    target_forbidden_raw = probe.model_copy(deep=True)
    target_forbidden_raw.expectations.forbidden_target_kinds.append("CLAIM")
    target_forbidden = score_fixture(target_forbidden_raw, output("PROBE", "COMPLEXITY", "CLAIM"))
    assert target_forbidden.forbidden_target_kind_used
    stale = score_fixture(fixture("stale-code-wait"), output("PROBE", "PROVE", "CODE_SNAPSHOT"))
    duplicate = score_fixture(
        fixture("two-sum-repeated-concept-wait"), output("PROBE", "PROVE", "CODE_SNAPSHOT")
    )
    assert stale.stale_behavior_violation and duplicate.duplicate_probe_violation
    internal_safe = score_fixture(probe, output("PROBE", "ASSUMPTION_CHALLENGE", "CLAIM"))
    assert not internal_safe.obvious_answer_leakage
    leak_fixture = probe.model_copy(deep=True)
    leak_fixture.expectations.must_not_reveal = ["actually guaranteed"]
    assert score_fixture(
        leak_fixture, output("PROBE", "ASSUMPTION_CHALLENGE", "CLAIM")
    ).obvious_answer_leakage


def test_aggregate_denominators_are_applicable() -> None:
    results = [
        score_fixture(
            item,
            output(
                item.expectations.expected_action,
                item.expectations.acceptable_strategies[0]
                if item.expectations.expected_action == "PROBE"
                else None,
                item.expectations.acceptable_target_kinds[0],
            ),
        )
        for item in load_fixtures()
    ]
    aggregate = aggregate_results(results)
    assert cast(dict[str, object], aggregate["strategy_appropriateness"])["denominator"] == 12
    assert cast(dict[str, object], aggregate["stale_decision_suppression"])["denominator"] == 2
    assert cast(dict[str, object], aggregate["duplicate_probe"])["denominator"] == 2
    assert cast(dict[str, object], aggregate["answer_leakage"])["denominator"] == sum(
        bool(item.expectations.must_not_reveal) for item in load_fixtures()
    )

    cloned = results[0].model_copy(
        update={
            "fixture_id": "synthetic-applicability",
            "strategy_applicable": True,
            "answer_leakage_applicable": True,
            "stale_suppression_applicable": True,
            "duplicate_suppression_applicable": True,
        }
    )
    expanded = aggregate_results([*results, cloned])
    assert cast(dict[str, object], expanded["strategy_appropriateness"])["denominator"] == 13
    assert cast(dict[str, object], expanded["stale_decision_suppression"])["denominator"] == 3
    assert cast(dict[str, object], expanded["duplicate_probe"])["denominator"] == 3
    original_leakage_denominator = cast(
        int, cast(dict[str, object], aggregate["answer_leakage"])["denominator"]
    )
    assert cast(dict[str, object], expanded["answer_leakage"])["denominator"] == (
        original_leakage_denominator + 1
    )


def test_live_evaluator_refuses_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERQ_STAGE4_LIVE_EVAL", raising=False)
    with patch("app.evals.examiner.live.build_reasoning_provider", new=AsyncMock()) as provider:
        with pytest.raises(RuntimeError, match="Refusing live evaluation"):
            asyncio.run(run_live_evaluation())
    provider.assert_not_called()


async def test_evaluation_conversion_and_scoring_do_not_mutate_interview_history(
    db_session: AsyncSession,
) -> None:
    before = (
        await db_session.scalar(select(func.count()).select_from(CandidateClaim)),
        await db_session.scalar(select(func.count()).select_from(ExaminerDecision)),
    )
    item = fixture("two-sum-hash-assumption")
    model_input_json(item.input)
    score_fixture(item, output("PROBE", "ASSUMPTION_CHALLENGE", "CLAIM"))
    after = (
        await db_session.scalar(select(func.count()).select_from(CandidateClaim)),
        await db_session.scalar(select(func.count()).select_from(ExaminerDecision)),
    )
    assert after == before

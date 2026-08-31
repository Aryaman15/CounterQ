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
from app.examiner.context import serialize_examiner_context
from app.examiner.models import CandidateClaim, ExaminerDecision


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
            },
        }
    )


def fixture(name: str) -> EvaluationFixture:
    return next(item for item in load_fixtures() if item.fixture_id == name)


def test_corpus_and_production_context_parity() -> None:
    fixtures = load_fixtures()
    assert len(fixtures) == 24
    assert {item.input.source_observation_type for item in fixtures} <= {
        "CANDIDATE_TRANSCRIPT_FINALIZED",
        "CODE_MEANINGFULLY_CHANGED",
    }
    context = evaluation_context_json(fixtures[0].input)
    expected = serialize_examiner_context(
        trusted_policy=cast(dict[str, object], context["trusted_policy"]),
        interview=cast(dict[str, object], context["interview"]),
        problem=cast(dict[str, object], context["problem"]),
        interview_pack=cast(dict[str, object], context["interview_pack"]),
        source_observation=cast(dict[str, object], context["source_observation"]),
        source_freshness=cast(dict[str, object], context["source_freshness"]),
        recent_history=cast(list[dict[str, object]], context["recent_history"]),
    )
    assert set(context) - {"evaluation_context_extension"} == set(expected)


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

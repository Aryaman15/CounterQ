import json
from pathlib import Path
from typing import cast

from app.interviews.assistance_policy import (
    COACH_ASSISTANCE_OUTPUT_CONTRACT,
    COACH_ASSISTANCE_POLICY_KEY,
    COACH_ASSISTANCE_POLICY_VERSION,
    CoachAssistanceOutput,
    coach_assistance_policy_descriptor,
)
from app.interviews.mode_policy import (
    ModePolicy,
    independence_for_hint_level,
    strongest_independence,
)


def _fixtures() -> list[dict[str, object]]:
    path = Path(__file__).parents[3] / "app" / "evals" / "assistance" / "fixtures.json"
    return cast(list[dict[str, object]], json.loads(path.read_text(encoding="utf-8")))


def test_stage6a_corpus_has_twenty_named_cases() -> None:
    fixtures = _fixtures()
    assert len(fixtures) >= 20
    assert len({item["fixture_id"] for item in fixtures}) == len(fixtures)
    required_stage6a_cases = {
        "simulation_hint_request",
        "coach_before_attempt",
        "coach_first_metacognitive",
        "coach_narrowing_after_gap",
        "coach_conceptual_after_failure",
        "coach_self_correction_suppression",
        "debugging_stall_targeted",
        "coach_structural_after_conceptual",
        "coach_direct_after_structural",
        "simulation_correctness_request",
        "coach_confirmation_with_evidence",
        "coach_defense_reserved",
        "coach_wrap_only",
        "assistance_unrelated_target",
        "assistance_same_target_retry",
        "authorized_assistance_undelivered",
        "partially_delivered_meaningful_assistance",
        "probe_followed_by_success",
        "teaching_immediate_repetition",
        "alternate_technically_correct_approach",
    }
    assert required_stage6a_cases.issubset({str(item["fixture_id"]) for item in fixtures})


def test_stage6a_mode_policy_matches_offline_corpus() -> None:
    policy = ModePolicy()
    for fixture in _fixtures():
        decision = policy.evaluate_assistance(
            mode=str(fixture["mode"]),
            stage=str(fixture["stage"]),
            time_pressure=fixture["time_pressure"],  # type: ignore[arg-type]
            meaningful_attempt_exists=bool(fixture["attempt"]),
            gap_evidence_exists=bool(fixture["gap"]),
            highest_delivered_level=(
                str(fixture["prior"]) if fixture["prior"] is not None else None
            ),
            correctness_confirmation=bool(fixture["confirm"]),
            sufficient_independent_evidence=bool(fixture["independent"]),
        )
        assert decision.allowed is fixture["expected_allowed"], fixture["fixture_id"]
        assert decision.next_hint_level == fixture["expected_level"], fixture["fixture_id"]
        assert decision.reason == fixture["expected_reason"], fixture["fixture_id"]


def test_assistance_policy_identity_and_strict_contract_are_pinned() -> None:
    descriptor = coach_assistance_policy_descriptor()
    assert descriptor.policy_key == COACH_ASSISTANCE_POLICY_KEY == "coach_assistance"
    assert descriptor.version == COACH_ASSISTANCE_POLICY_VERSION == "v1"
    output = CoachAssistanceOutput(
        contract_version=COACH_ASSISTANCE_OUTPUT_CONTRACT,
        prompt_text="Which invariant are you least certain about?",
    )
    assert output.contract_version == "coach-assistance-output.v1"


def test_hint_level_independence_mapping_is_frozen() -> None:
    assert independence_for_hint_level("METACOGNITIVE") == "AFTER_LIGHT_GUIDANCE"
    assert independence_for_hint_level("PROBLEM_NARROWING") == "AFTER_LIGHT_GUIDANCE"
    assert independence_for_hint_level("CONCEPTUAL_HINT") == "AFTER_LIGHT_GUIDANCE"
    assert independence_for_hint_level("STRUCTURAL_HINT") == "AFTER_STRONG_HINT"
    assert independence_for_hint_level("DIRECT_TEACHING") == "DIRECTLY_TAUGHT"
    assert (
        strongest_independence(["AFTER_PROBE", "AFTER_LIGHT_GUIDANCE", "AFTER_STRONG_HINT"])
        == "AFTER_STRONG_HINT"
    )


def test_delivery_and_target_fixture_expectations_follow_canonical_attribution() -> None:
    for fixture in _fixtures():
        expected = fixture.get("expected_attribution")
        if expected is None:
            continue
        if fixture.get("delivery_truth") == "AUTHORIZED" or not fixture.get("target_match", True):
            actual = "INDEPENDENT"
        elif fixture.get("prompt_kind") == "PROBE":
            actual = "AFTER_PROBE"
        else:
            actual = independence_for_hint_level(str(fixture["expected_level"]))
        assert actual == expected, fixture["fixture_id"]

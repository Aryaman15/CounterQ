"""Deterministic Coach/Simulation assistance policy.

The policy selects legality and the maximum permissible help. It never writes
candidate-visible wording and never asks a model to decide authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from app.interviews.time_policy import TimePressure

InterviewMode = Literal["COACH", "SIMULATION"]
HintLevel = Literal[
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
]

HINT_LADDER: tuple[HintLevel, ...] = (
    "METACOGNITIVE",
    "PROBLEM_NARROWING",
    "CONCEPTUAL_HINT",
    "STRUCTURAL_HINT",
    "DIRECT_TEACHING",
)

ASSISTANCE_ALLOWED_STAGES = frozenset(
    {
        "PROBLEM_UNDERSTANDING",
        "APPROACH_DISCOVERY",
        "APPROACH_DEFENSE",
        "IMPLEMENTATION",
        "TESTING_DEBUGGING",
        "COMPLEXITY_EDGE_CASES",
        "CONSTRAINT_MUTATION",
        "FINAL_DEFENSE",
    }
)


@dataclass(frozen=True)
class AssistanceBudgetConfiguration:
    max_assistance_interventions: int
    max_structural_hints: int
    max_direct_teaching_interventions: int
    max_guided_retries: int


@dataclass(frozen=True)
class ModePolicyDecision:
    allowed: bool
    reason: str
    next_hint_level: HintLevel | None
    maximum_hint_level: HintLevel | None
    requires_gap_evidence: bool
    may_confirm_correctness: bool
    may_offer_direct_teaching: bool


class ModePolicy:
    """The shared interview engine's deterministic mode overlay."""

    policy_version = "mode-policy.v1"

    def assistance_budget(self, mode: str) -> AssistanceBudgetConfiguration:
        self._validate_mode(mode)
        if mode == "SIMULATION":
            return AssistanceBudgetConfiguration(0, 0, 0, 0)
        return AssistanceBudgetConfiguration(6, 2, 1, 2)

    def assistance_request_allowed(self, mode: str) -> bool:
        self._validate_mode(mode)
        return mode == "COACH"

    def correctness_confirmation_allowed(
        self, *, mode: str, sufficient_independent_evidence: bool
    ) -> bool:
        self._validate_mode(mode)
        return mode == "COACH" and sufficient_independent_evidence

    def solution_assistance_allowed(self, mode: str) -> bool:
        self._validate_mode(mode)
        return mode == "COACH"

    def direct_teaching_allowed(
        self,
        *,
        mode: str,
        stage: str,
        time_pressure: TimePressure,
        gap_evidence_exists: bool,
        prior_lower_level_assistance_failed: bool,
    ) -> bool:
        self._validate_mode(mode)
        return (
            mode == "COACH"
            and stage in ASSISTANCE_ALLOWED_STAGES
            and stage != "FINAL_DEFENSE"
            and time_pressure == "NORMAL"
            and gap_evidence_exists
            and prior_lower_level_assistance_failed
        )

    def guided_retry_allowed(self, *, mode: str, remaining_budget: int) -> bool:
        self._validate_mode(mode)
        return mode == "COACH" and remaining_budget > 0

    def factual_clarification_allowed(self, mode: str, *, solution_directed: bool = False) -> bool:
        self._validate_mode(mode)
        return not solution_directed

    @staticmethod
    def solution_guidance_is_assistance() -> bool:
        return True

    def evaluate_assistance(
        self,
        *,
        mode: str,
        stage: str,
        time_pressure: TimePressure,
        meaningful_attempt_exists: bool,
        gap_evidence_exists: bool,
        highest_delivered_level: str | None,
        correctness_confirmation: bool = False,
        sufficient_independent_evidence: bool = False,
        initial_final_defense_answer_captured: bool = False,
    ) -> ModePolicyDecision:
        self._validate_mode(mode)
        if mode == "SIMULATION":
            return self._deny("SIMULATION_ASSISTANCE_PROHIBITED")
        if stage not in ASSISTANCE_ALLOWED_STAGES:
            return self._deny("STAGE_PROHIBITS_ASSISTANCE")
        if stage == "FINAL_DEFENSE" and not initial_final_defense_answer_captured:
            return self._deny("FINAL_DEFENSE_INITIAL_ANSWER_REQUIRED")
        if time_pressure in {"DEFENSE_RESERVED", "WRAP_ONLY"}:
            return self._deny(f"{time_pressure}_PROHIBITS_ASSISTANCE")
        if not meaningful_attempt_exists:
            return self._deny("MEANINGFUL_ATTEMPT_REQUIRED")
        if correctness_confirmation and not sufficient_independent_evidence:
            return self._deny("INDEPENDENT_EVIDENCE_REQUIRED_FOR_CONFIRMATION")

        maximum: HintLevel = (
            "CONCEPTUAL_HINT"
            if time_pressure == "CONSTRAINED" or stage == "FINAL_DEFENSE"
            else "DIRECT_TEACHING"
        )
        next_level = self.next_level(highest_delivered_level)
        if not gap_evidence_exists and next_level != "METACOGNITIVE":
            next_level = "METACOGNITIVE"
        if HINT_LADDER.index(next_level) > HINT_LADDER.index(maximum):
            return self._deny("TIME_PRESSURE_CAP_REACHED", maximum=maximum)
        return ModePolicyDecision(
            allowed=True,
            reason="ASSISTANCE_ALLOWED",
            next_hint_level=next_level,
            maximum_hint_level=maximum,
            requires_gap_evidence=next_level != "METACOGNITIVE",
            may_confirm_correctness=self.correctness_confirmation_allowed(
                mode=mode,
                sufficient_independent_evidence=sufficient_independent_evidence,
            ),
            may_offer_direct_teaching=maximum == "DIRECT_TEACHING",
        )

    @staticmethod
    def next_level(highest_delivered_level: str | None) -> HintLevel:
        if highest_delivered_level is None:
            return "METACOGNITIVE"
        if highest_delivered_level not in HINT_LADDER:
            raise ValueError("Unknown delivered assistance hint level")
        index = HINT_LADDER.index(cast(HintLevel, highest_delivered_level))
        return HINT_LADDER[min(index + 1, len(HINT_LADDER) - 1)]

    @staticmethod
    def _validate_mode(mode: str) -> None:
        if mode not in {"COACH", "SIMULATION"}:
            raise ValueError("Unknown InterviewConfiguration mode")

    def _deny(self, reason: str, *, maximum: HintLevel | None = None) -> ModePolicyDecision:
        return ModePolicyDecision(
            allowed=False,
            reason=reason,
            next_hint_level=None,
            maximum_hint_level=maximum,
            requires_gap_evidence=False,
            may_confirm_correctness=False,
            may_offer_direct_teaching=False,
        )


def independence_for_hint_level(hint_level: str) -> str:
    if hint_level in {"METACOGNITIVE", "PROBLEM_NARROWING", "CONCEPTUAL_HINT"}:
        return "AFTER_LIGHT_GUIDANCE"
    if hint_level == "STRUCTURAL_HINT":
        return "AFTER_STRONG_HINT"
    if hint_level == "DIRECT_TEACHING":
        return "DIRECTLY_TAUGHT"
    raise ValueError("Unknown assistance hint level")


def strongest_independence(levels: list[str]) -> str:
    precedence = {
        "INDEPENDENT": 0,
        "AFTER_PROBE": 1,
        "AFTER_LIGHT_GUIDANCE": 2,
        "AFTER_STRONG_HINT": 3,
        "DIRECTLY_TAUGHT": 4,
    }
    if not levels or any(level not in precedence for level in levels):
        raise ValueError("Independence precedence requires known levels")
    return max(levels, key=precedence.__getitem__)

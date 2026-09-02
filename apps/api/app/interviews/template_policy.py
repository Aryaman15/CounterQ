"""Configuration-driven interview templates; lifecycle edges remain shared."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InterviewTemplate = Literal[
    "QUICK_DRILL",
    "SOLUTION_DEFENSE",
    "STANDARD_CODING_INTERVIEW",
    "FULL_SIMULATION",
]


@dataclass(frozen=True)
class StagePlanEntry:
    stage: str
    target_seconds: int
    skippable: bool = False
    compression_priority: int = 0


@dataclass(frozen=True)
class InterviewTemplatePolicy:
    template: InterviewTemplate
    configured_duration_seconds: int | None
    stage_plan: tuple[StagePlanEntry, ...]
    protected_final_defense_seconds: int
    protected_wrap_up_seconds: int
    max_probes: int
    max_deep_reasoning_calls: int
    reserved_post_interview_deep_reasoning_calls: int
    max_strong_reasoning_calls: int

    def __post_init__(self) -> None:
        if not 0 <= self.reserved_post_interview_deep_reasoning_calls <= (
            self.max_deep_reasoning_calls
        ):
            raise ValueError("Post-interview reserve must be within the total deep budget")

    @property
    def protected_downstream_seconds(self) -> int:
        return self.protected_final_defense_seconds + self.protected_wrap_up_seconds


STANDARD_STAGE_PLAN = (
    StagePlanEntry("INTRODUCTION", 60),
    StagePlanEntry("PROBLEM_UNDERSTANDING", 120),
    StagePlanEntry("APPROACH_DISCOVERY", 240),
    StagePlanEntry("APPROACH_DEFENSE", 150),
    StagePlanEntry("IMPLEMENTATION", 600),
    StagePlanEntry("TESTING_DEBUGGING", 240),
    StagePlanEntry("COMPLEXITY_EDGE_CASES", 150),
    StagePlanEntry("CONSTRAINT_MUTATION", 60, skippable=True, compression_priority=1),
    StagePlanEntry("FINAL_DEFENSE", 120),
    StagePlanEntry("WRAP_UP", 60),
)

TEMPLATE_POLICIES: dict[InterviewTemplate, InterviewTemplatePolicy] = {
    "QUICK_DRILL": InterviewTemplatePolicy(
        template="QUICK_DRILL",
        configured_duration_seconds=600,
        stage_plan=(),
        protected_final_defense_seconds=120,
        protected_wrap_up_seconds=60,
        max_probes=5,
        max_deep_reasoning_calls=16,
        reserved_post_interview_deep_reasoning_calls=8,
        max_strong_reasoning_calls=1,
    ),
    "SOLUTION_DEFENSE": InterviewTemplatePolicy(
        template="SOLUTION_DEFENSE",
        configured_duration_seconds=900,
        stage_plan=(),
        protected_final_defense_seconds=120,
        protected_wrap_up_seconds=60,
        max_probes=5,
        max_deep_reasoning_calls=20,
        reserved_post_interview_deep_reasoning_calls=12,
        max_strong_reasoning_calls=1,
    ),
    "STANDARD_CODING_INTERVIEW": InterviewTemplatePolicy(
        template="STANDARD_CODING_INTERVIEW",
        configured_duration_seconds=1800,
        stage_plan=STANDARD_STAGE_PLAN,
        protected_final_defense_seconds=120,
        protected_wrap_up_seconds=60,
        max_probes=5,
        max_deep_reasoning_calls=24,
        reserved_post_interview_deep_reasoning_calls=16,
        max_strong_reasoning_calls=1,
    ),
    "FULL_SIMULATION": InterviewTemplatePolicy(
        template="FULL_SIMULATION",
        configured_duration_seconds=None,
        stage_plan=(),
        protected_final_defense_seconds=120,
        protected_wrap_up_seconds=60,
        max_probes=5,
        max_deep_reasoning_calls=24,
        reserved_post_interview_deep_reasoning_calls=16,
        max_strong_reasoning_calls=1,
    ),
}


def template_policy(template: InterviewTemplate) -> InterviewTemplatePolicy:
    return TEMPLATE_POLICIES[template]


def template_for_duration(configured_duration_seconds: int) -> InterviewTemplatePolicy | None:
    return next(
        (
            policy
            for policy in TEMPLATE_POLICIES.values()
            if policy.configured_duration_seconds == configured_duration_seconds
        ),
        None,
    )

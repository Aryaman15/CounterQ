from __future__ import annotations

from dataclasses import dataclass

from app.db.constants import INTERVIEW_STAGES

STATE_MACHINE_POLICY_VERSION = "stage1.2-state-machine.v1"


class StateMachineError(ValueError):
    pass


class IllegalStageTransition(StateMachineError):
    pass


@dataclass(frozen=True)
class TransitionContext:
    trigger: str
    defense_reserve_reached: bool = False
    wrap_only: bool = False
    candidate_requested_finish: bool = False
    mutation_skipped: bool = False
    substantive_fix_required: bool = False


ACTIVE_STAGES = (
    "INTRODUCTION",
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DISCOVERY",
    "APPROACH_DEFENSE",
    "IMPLEMENTATION",
    "TESTING_DEBUGGING",
    "COMPLEXITY_EDGE_CASES",
    "CONSTRAINT_MUTATION",
    "FINAL_DEFENSE",
    "WRAP_UP",
)

LINEAR_TRANSITIONS = {
    ("SETUP", "INTRODUCTION"),
    ("INTRODUCTION", "PROBLEM_UNDERSTANDING"),
    ("PROBLEM_UNDERSTANDING", "APPROACH_DISCOVERY"),
    ("APPROACH_DISCOVERY", "APPROACH_DEFENSE"),
    ("APPROACH_DEFENSE", "IMPLEMENTATION"),
    ("IMPLEMENTATION", "TESTING_DEBUGGING"),
    ("TESTING_DEBUGGING", "COMPLEXITY_EDGE_CASES"),
    ("COMPLEXITY_EDGE_CASES", "CONSTRAINT_MUTATION"),
    ("CONSTRAINT_MUTATION", "FINAL_DEFENSE"),
    ("FINAL_DEFENSE", "WRAP_UP"),
    ("WRAP_UP", "COMPLETED"),
}


def is_valid_stage(stage: str) -> bool:
    return stage in INTERVIEW_STAGES


def can_transition(from_stage: str, to_stage: str, context: TransitionContext) -> bool:
    if not is_valid_stage(from_stage) or not is_valid_stage(to_stage):
        return False
    if from_stage == "COMPLETED" or from_stage == to_stage:
        return False
    if (from_stage, to_stage) in LINEAR_TRANSITIONS:
        return True
    if from_stage == "APPROACH_DEFENSE" and to_stage == "APPROACH_DISCOVERY":
        return True
    if from_stage == "TESTING_DEBUGGING" and to_stage == "IMPLEMENTATION":
        return context.substantive_fix_required or context.trigger == "SUBSTANTIVE_FIX"
    if from_stage == "COMPLEXITY_EDGE_CASES" and to_stage == "IMPLEMENTATION":
        return context.substantive_fix_required or context.trigger == "SUBSTANTIVE_FIX"
    if from_stage == "COMPLEXITY_EDGE_CASES" and to_stage == "FINAL_DEFENSE":
        return context.mutation_skipped or context.trigger == "MUTATION_SKIPPED"
    if to_stage == "FINAL_DEFENSE" and from_stage in (
        "APPROACH_DISCOVERY",
        "APPROACH_DEFENSE",
        "IMPLEMENTATION",
        "TESTING_DEBUGGING",
        "COMPLEXITY_EDGE_CASES",
    ):
        return context.defense_reserve_reached or context.trigger == "DEFENSE_RESERVE"
    if to_stage == "WRAP_UP" and from_stage in ACTIVE_STAGES:
        return (
            context.wrap_only
            or context.candidate_requested_finish
            or context.trigger in {"WRAP_ONLY", "CANDIDATE_REQUESTED_FINISH"}
        )
    return False


def require_transition(from_stage: str, to_stage: str, context: TransitionContext) -> None:
    if not can_transition(from_stage, to_stage, context):
        raise IllegalStageTransition(f"Illegal interview transition: {from_stage} -> {to_stage}")

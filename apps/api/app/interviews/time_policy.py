"""Pure server-side schedule projection. It never persists timer ticks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.interviews.template_policy import InterviewTemplatePolicy

TimePressure = Literal["NORMAL", "CONSTRAINED", "DEFENSE_RESERVED", "WRAP_ONLY"]


@dataclass(frozen=True)
class TimePolicyResult:
    time_remaining_seconds: int
    stage_elapsed_seconds: int
    pressure: TimePressure
    target_allocation_reached: bool
    protected_final_defense_reserve_reached: bool
    wrap_only: bool
    mutation_should_skip: bool
    optional_probes_suppressed: bool


def evaluate_time_policy(
    *,
    policy: InterviewTemplatePolicy,
    current_stage: str,
    stage_started_at: datetime,
    deadline_at: datetime,
    now: datetime,
) -> TimePolicyResult:
    remaining = max(0, int((deadline_at - now).total_seconds()))
    elapsed = max(0, int((now - stage_started_at).total_seconds()))
    target = next(
        (entry.target_seconds for entry in policy.stage_plan if entry.stage == current_stage),
        0,
    )
    wrap_only = remaining <= policy.protected_wrap_up_seconds
    defense_reserved = remaining <= policy.protected_downstream_seconds
    constrained = defense_reserved or (
        target > 0
        and elapsed >= target
        and remaining <= policy.protected_downstream_seconds + target
    )
    pressure: TimePressure = (
        "WRAP_ONLY"
        if wrap_only
        else "DEFENSE_RESERVED"
        if defense_reserved
        else "CONSTRAINED"
        if constrained
        else "NORMAL"
    )
    return TimePolicyResult(
        time_remaining_seconds=remaining,
        stage_elapsed_seconds=elapsed,
        pressure=pressure,
        target_allocation_reached=target > 0 and elapsed >= target,
        protected_final_defense_reserve_reached=defense_reserved,
        wrap_only=wrap_only,
        mutation_should_skip=current_stage == "COMPLEXITY_EDGE_CASES" and defense_reserved,
        optional_probes_suppressed=pressure != "NORMAL",
    )

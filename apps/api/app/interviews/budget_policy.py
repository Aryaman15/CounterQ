from __future__ import annotations

from dataclasses import dataclass

from app.interviews.models import SessionBudget


@dataclass(frozen=True)
class BudgetAvailability:
    probe_available: bool
    deep_reasoning_available: bool
    strong_reasoning_available: bool
    vision_available: bool
    duration_seconds: int


def budget_availability(budget: SessionBudget) -> BudgetAvailability:
    return BudgetAvailability(
        probe_available=budget.probes_used < budget.max_probes,
        deep_reasoning_available=budget.deep_reasoning_used < budget.max_deep_reasoning_calls,
        strong_reasoning_available=budget.strong_reasoning_used < budget.max_strong_reasoning_calls,
        vision_available=budget.vision_used < budget.max_vision_calls,
        duration_seconds=budget.max_duration_seconds,
    )

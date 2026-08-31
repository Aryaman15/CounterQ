from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewerPrompt, SessionBudget


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


@dataclass(frozen=True)
class ProbeBudgetSnapshot:
    max_probes: int
    probes_used: int
    outstanding_authorized_probes: int
    remaining_probes: int


async def probe_budget_snapshot(
    session: AsyncSession,
    session_id: object,
    *,
    for_update: bool = False,
) -> ProbeBudgetSnapshot | None:
    """Return the one authoritative probe-availability calculation.

    An authorized but not yet meaningfully delivered probe reserves capacity.
    Delivered probes are represented by ``probes_used`` and no longer have
    ``AUTHORIZED`` status, so the two terms do not double-count.
    """
    statement = select(SessionBudget).where(SessionBudget.session_id == session_id)
    if for_update:
        statement = statement.with_for_update()
    budget = await session.scalar(statement)
    if budget is None:
        return None
    outstanding = int(
        await session.scalar(
            select(func.count())
            .select_from(InterviewerPrompt)
            .where(InterviewerPrompt.interview_session_id == session_id)
            .where(InterviewerPrompt.kind == "PROBE")
            .where(InterviewerPrompt.status == "AUTHORIZED")
        )
        or 0
    )
    remaining = max(0, budget.max_probes - budget.probes_used - outstanding)
    return ProbeBudgetSnapshot(
        max_probes=budget.max_probes,
        probes_used=budget.probes_used,
        outstanding_authorized_probes=outstanding,
        remaining_probes=remaining,
    )

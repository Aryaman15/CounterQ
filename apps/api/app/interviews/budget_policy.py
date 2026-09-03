from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

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


def interactive_deep_reasoning_limit(budget: SessionBudget) -> int:
    """Return the portion of the total deep-reasoning budget available live."""
    return budget.max_deep_reasoning_calls - budget.reserved_post_interview_deep_reasoning_calls


def budget_availability(budget: SessionBudget) -> BudgetAvailability:
    return BudgetAvailability(
        probe_available=budget.probes_used < budget.max_probes,
        deep_reasoning_available=(
            budget.deep_reasoning_used < interactive_deep_reasoning_limit(budget)
        ),
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


@dataclass(frozen=True)
class AssistanceBudgetSnapshot:
    max_assistance_interventions: int
    assistance_interventions_used: int
    outstanding_assistance_interventions: int
    remaining_assistance_interventions: int
    max_structural_hints: int
    structural_hints_used: int
    outstanding_structural_hints: int
    remaining_structural_hints: int
    max_direct_teaching_interventions: int
    direct_teaching_interventions_used: int
    outstanding_direct_teaching_interventions: int
    remaining_direct_teaching_interventions: int
    max_guided_retries: int
    guided_retries_used: int
    outstanding_guided_retries: int
    remaining_guided_retries: int


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


async def assistance_budget_snapshot(
    session: AsyncSession,
    session_id: object,
    *,
    for_update: bool = False,
    exclude_prompt_id: UUID | None = None,
) -> AssistanceBudgetSnapshot | None:
    """Count delivered usage plus PROPOSED/AUTHORIZED reservations."""

    statement = select(SessionBudget).where(SessionBudget.session_id == session_id)
    if for_update:
        statement = statement.with_for_update()
    budget = await session.scalar(statement)
    if budget is None:
        return None
    total_reserved = await _outstanding_assistance(
        session, session_id, exclude_prompt_id=exclude_prompt_id
    )
    structural_reserved = await _outstanding_assistance(
        session,
        session_id,
        hint_level="STRUCTURAL_HINT",
        exclude_prompt_id=exclude_prompt_id,
    )
    teaching_reserved = await _outstanding_assistance(
        session,
        session_id,
        hint_level="DIRECT_TEACHING",
        exclude_prompt_id=exclude_prompt_id,
    )
    retry_reserved = await _outstanding_assistance(
        session, session_id, guided_retry=True, exclude_prompt_id=exclude_prompt_id
    )
    return AssistanceBudgetSnapshot(
        max_assistance_interventions=budget.max_assistance_interventions,
        assistance_interventions_used=budget.assistance_interventions_used,
        outstanding_assistance_interventions=total_reserved,
        remaining_assistance_interventions=max(
            0,
            budget.max_assistance_interventions
            - budget.assistance_interventions_used
            - total_reserved,
        ),
        max_structural_hints=budget.max_structural_hints,
        structural_hints_used=budget.structural_hints_used,
        outstanding_structural_hints=structural_reserved,
        remaining_structural_hints=max(
            0,
            budget.max_structural_hints - budget.structural_hints_used - structural_reserved,
        ),
        max_direct_teaching_interventions=budget.max_direct_teaching_interventions,
        direct_teaching_interventions_used=budget.direct_teaching_interventions_used,
        outstanding_direct_teaching_interventions=teaching_reserved,
        remaining_direct_teaching_interventions=max(
            0,
            budget.max_direct_teaching_interventions
            - budget.direct_teaching_interventions_used
            - teaching_reserved,
        ),
        max_guided_retries=budget.max_guided_retries,
        guided_retries_used=budget.guided_retries_used,
        outstanding_guided_retries=retry_reserved,
        remaining_guided_retries=max(
            0,
            budget.max_guided_retries - budget.guided_retries_used - retry_reserved,
        ),
    )


def assistance_capacity_available(
    snapshot: AssistanceBudgetSnapshot,
    *,
    hint_level: str,
    invites_guided_retry: bool,
) -> bool:
    if snapshot.remaining_assistance_interventions == 0:
        return False
    if hint_level == "STRUCTURAL_HINT" and snapshot.remaining_structural_hints == 0:
        return False
    if hint_level == "DIRECT_TEACHING" and snapshot.remaining_direct_teaching_interventions == 0:
        return False
    return not invites_guided_retry or snapshot.remaining_guided_retries > 0


async def _outstanding_assistance(
    session: AsyncSession,
    session_id: object,
    *,
    hint_level: str | None = None,
    guided_retry: bool = False,
    exclude_prompt_id: UUID | None = None,
) -> int:
    statement = (
        select(func.count(InterviewerPrompt.id))
        .where(InterviewerPrompt.interview_session_id == session_id)
        .where(InterviewerPrompt.status.in_(("PROPOSED", "AUTHORIZED")))
        .where(InterviewerPrompt.assistance_type.is_not(None))
    )
    if exclude_prompt_id is not None:
        statement = statement.where(InterviewerPrompt.id != exclude_prompt_id)
    if hint_level is not None:
        statement = statement.where(InterviewerPrompt.hint_level == hint_level)
    if guided_retry:
        statement = statement.where(InterviewerPrompt.invites_guided_retry.is_(True))
    return int((await session.execute(statement)).scalar_one())

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.examiner.coordinator import LiveExaminerCoordinator, LiveExaminerDebugResult
from app.interviews.models import InterviewerPrompt
from app.interviews.prompt_authorization import (
    AUTHORIZED_PROMPT_DELIVERY_WINDOW_SECONDS,
    PromptAuthorizationService,
    PromptGateResult,
)


@dataclass(frozen=True)
class DevelopmentPolicyGateTiming:
    analysis_completed_at: datetime
    gate_evaluated_at: datetime | None
    decision_deadline_at: datetime | None
    remaining_usefulness_seconds_at_analysis: float | None
    remaining_usefulness_seconds_at_gate: float | None
    authorized_at: datetime | None
    delivery_window_expires_at: datetime | None
    delivery_window_seconds: float
    delivery_window_state: str | None


@dataclass(frozen=True)
class DevelopmentAnalyzeAndAuthorizeResult:
    analysis: LiveExaminerDebugResult
    policy_gate: PromptGateResult | None
    timing: DevelopmentPolicyGateTiming


BeforePolicyGateHook = Callable[[LiveExaminerDebugResult], Awaitable[None]]


class DevelopmentAnalyzeAndAuthorizeWorkflow:
    """Development harness orchestration for the future production live handoff."""

    def __init__(
        self,
        *,
        coordinator: LiveExaminerCoordinator,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
        authorized_prompt_delivery_window_seconds: float = (
            AUTHORIZED_PROMPT_DELIVERY_WINDOW_SECONDS
        ),
        before_policy_gate: BeforePolicyGateHook | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._sessionmaker = sessionmaker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._authorized_prompt_delivery_window_seconds = (
            authorized_prompt_delivery_window_seconds
        )
        self._authorized_prompt_delivery_window = timedelta(
            seconds=authorized_prompt_delivery_window_seconds,
        )
        self._before_policy_gate = before_policy_gate

    async def analyze_and_authorize_latest(
        self,
        interview_session_id: UUID,
    ) -> DevelopmentAnalyzeAndAuthorizeResult:
        analysis = await self._coordinator.analyze_latest(interview_session_id)
        analysis_completed_at = self._clock()
        decision_deadline_at = analysis.decision.deadline_at if analysis.decision else None

        if analysis.decision is None or analysis.decision.status != "PROPOSED":
            return DevelopmentAnalyzeAndAuthorizeResult(
                analysis=analysis,
                policy_gate=None,
                timing=self._timing(
                    analysis_completed_at=analysis_completed_at,
                    gate_evaluated_at=None,
                    decision_deadline_at=decision_deadline_at,
                    authorized_at=None,
                    delivery_window_state=None,
                ),
            )

        if self._before_policy_gate is not None:
            await self._before_policy_gate(analysis)

        async with self._sessionmaker() as session:
            async with session.begin():
                gate = await PromptAuthorizationService(
                    session,
                    clock=self._clock,
                    authorized_prompt_delivery_window_seconds=(
                        self._authorized_prompt_delivery_window_seconds
                    ),
                ).evaluate_examiner_decision(
                    session_id=interview_session_id,
                    decision_id=analysis.decision.id,
                )
                authorized_at: datetime | None = None
                if gate.prompt_id is not None:
                    prompt = await session.get(InterviewerPrompt, gate.prompt_id)
                    authorized_at = prompt.authorized_at if prompt else None
                gate_evaluated_at = self._clock()

        return DevelopmentAnalyzeAndAuthorizeResult(
            analysis=analysis,
            policy_gate=gate,
            timing=self._timing(
                analysis_completed_at=analysis_completed_at,
                gate_evaluated_at=gate_evaluated_at,
                decision_deadline_at=decision_deadline_at,
                authorized_at=authorized_at,
                delivery_window_state=_delivery_window_state(
                    gate=gate,
                    authorized_at=authorized_at,
                    now=gate_evaluated_at,
                    window=self._authorized_prompt_delivery_window,
                ),
            ),
        )

    def _timing(
        self,
        *,
        analysis_completed_at: datetime,
        gate_evaluated_at: datetime | None,
        decision_deadline_at: datetime | None,
        authorized_at: datetime | None,
        delivery_window_state: str | None,
    ) -> DevelopmentPolicyGateTiming:
        return DevelopmentPolicyGateTiming(
            analysis_completed_at=analysis_completed_at,
            gate_evaluated_at=gate_evaluated_at,
            decision_deadline_at=decision_deadline_at,
            remaining_usefulness_seconds_at_analysis=_remaining_seconds(
                decision_deadline_at,
                analysis_completed_at,
            ),
            remaining_usefulness_seconds_at_gate=_remaining_seconds(
                decision_deadline_at,
                gate_evaluated_at,
            ),
            authorized_at=authorized_at,
            delivery_window_expires_at=(
                authorized_at + self._authorized_prompt_delivery_window
                if authorized_at is not None
                else None
            ),
            delivery_window_seconds=self._authorized_prompt_delivery_window_seconds,
            delivery_window_state=delivery_window_state,
        )


def _remaining_seconds(deadline: datetime | None, now: datetime | None) -> float | None:
    if deadline is None or now is None:
        return None
    return (deadline - now).total_seconds()


def _delivery_window_state(
    *,
    gate: PromptGateResult,
    authorized_at: datetime | None,
    now: datetime,
    window: timedelta,
) -> str | None:
    if gate.prompt_id is None:
        return None
    if gate.disposition != "AUTHORIZED" or authorized_at is None:
        return "UNAVAILABLE"
    if now > authorized_at + window:
        return "EXPIRED"
    return "OPEN"

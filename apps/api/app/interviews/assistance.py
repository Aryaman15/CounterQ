"""Candidate-requested Coach assistance orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.evidence.models import Evidence, EvidenceConcept, EvidenceSkill
from app.examiner.models import CandidateClaim
from app.interviews.budget_policy import (
    AssistanceBudgetSnapshot,
    assistance_budget_snapshot,
    assistance_capacity_available,
)
from app.interviews.interaction_repository import InterviewInteractionRepository
from app.interviews.mode_policy import HINT_LADDER, ModePolicy
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
)
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
from app.observation.models import CodeSnapshot, InterviewEvent

AssistanceRequestStatus = Literal[
    "AUTHORIZED",
    "REFUSED",
    "ATTEMPT_REQUIRED",
    "DEFERRED",
    "DENIED",
]


@dataclass(frozen=True)
class AssistanceRequestCommand:
    interview_session_id: UUID
    idempotency_key: str
    trigger: str = "CANDIDATE_REQUEST"
    assistance_type: str | None = None


@dataclass(frozen=True)
class AssistanceRequestResult:
    status: AssistanceRequestStatus
    reason: str
    mode: str
    request_event_id: UUID
    request_event_watermark: int
    interviewer_prompt_id: UUID | None
    prompt_kind: str | None
    assistance_type: str | None
    hint_level: str | None
    target_concept_id: UUID | None
    target_skill_dimension_id: UUID | None
    source_code_snapshot_id: UUID | None
    invites_guided_retry: bool
    budget: AssistanceBudgetSnapshot


@dataclass(frozen=True)
class _RequestFacts:
    session_id: UUID
    event_id: UUID
    watermark: int
    mode: str
    source_code_snapshot_id: UUID | None
    meaningful_attempt_exists: bool


@dataclass(frozen=True)
class _DiagnosticTarget:
    evidence_id: UUID
    concept_id: UUID | None
    skill_dimension_id: UUID | None
    finding: str
    created_at: datetime


class CoachAssistanceWorkflow:
    """Persist request, optionally checkpoint Evidence, then authorize safely."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        evidence_coordinator: SessionEvidenceEvaluationCoordinator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._evidence_coordinator = evidence_coordinator
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mode_policy = ModePolicy()

    async def request(self, command: AssistanceRequestCommand) -> AssistanceRequestResult:
        facts = await self._persist_request(command)
        existing = await self._existing_assistance_result(facts)
        if existing is not None:
            return existing

        if facts.mode == "COACH" and facts.meaningful_attempt_exists:
            if self._evidence_coordinator is not None:
                await self._evidence_coordinator.evaluate_active_checkpoint(
                    command.interview_session_id
                )
        return await self._authorize(command, facts)

    async def _persist_request(self, command: AssistanceRequestCommand) -> _RequestFacts:
        async with self._sessionmaker() as session:
            async with session.begin():
                runtime = InterviewRuntime(session, clock=self._clock)
                interview = await runtime.ensure_activity_allowed(command.interview_session_id)
                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                if configuration is None:
                    raise ValueError("InterviewConfiguration was not found")
                latest_snapshot = await session.scalar(
                    select(CodeSnapshot)
                    .where(CodeSnapshot.interview_session_id == interview.id)
                    .order_by(CodeSnapshot.version_number.desc())
                    .limit(1)
                )
                accepted = await runtime.accept_event(
                    AcceptEventCommand(
                        session_id=interview.id,
                        event_type="CANDIDATE_ASSISTANCE_REQUESTED",
                        source="SYSTEM",
                        occurred_at=self._clock(),
                        idempotency_key=command.idempotency_key,
                        payload={
                            "trigger": command.trigger,
                            "request_source": "INTERVIEW_ROOM",
                            **(
                                {"requested_assistance_type": command.assistance_type}
                                if command.assistance_type is not None
                                else {}
                            ),
                        },
                        provenance={"mode_policy_version": self._mode_policy.policy_version},
                        schema_version="candidate.assistance-request.v1",
                        expected_state_version=interview.state_version,
                        code_snapshot_id=latest_snapshot.id if latest_snapshot else None,
                    )
                )
                attempt = await _meaningful_attempt_exists(
                    session, interview.id, before_sequence=accepted.event.server_sequence
                )
                return _RequestFacts(
                    session_id=interview.id,
                    event_id=accepted.event.id,
                    watermark=accepted.event.server_sequence,
                    mode=configuration.mode,
                    source_code_snapshot_id=latest_snapshot.id if latest_snapshot else None,
                    meaningful_attempt_exists=attempt,
                )

    async def _existing_assistance_result(
        self, facts: _RequestFacts
    ) -> AssistanceRequestResult | None:
        async with self._sessionmaker() as session:
            prompt = await _prompt_for_request(session, facts)
            if prompt is None:
                return None
            budget = await assistance_budget_snapshot(session, prompt.interview_session_id)
            assert budget is not None
            status, reason = _existing_request_outcome(prompt, facts)
            return _result_from_prompt(
                status=status,
                reason=reason,
                mode=facts.mode,
                facts=facts,
                prompt=prompt,
                budget=budget,
            )

    async def _authorize(
        self, command: AssistanceRequestCommand, facts: _RequestFacts
    ) -> AssistanceRequestResult:
        async with self._sessionmaker() as session:
            async with session.begin():
                interview = await session.scalar(
                    select(InterviewSession)
                    .where(InterviewSession.id == command.interview_session_id)
                    .with_for_update()
                )
                if interview is None or interview.status != "ACTIVE":
                    raise ValueError("InterviewSession is no longer active")
                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                if configuration is None or configuration.mode != facts.mode:
                    raise ValueError("Interview mode changed during assistance request")
                budget = await assistance_budget_snapshot(session, interview.id, for_update=True)
                if budget is None:
                    raise ValueError("SessionBudget was not found")
                existing_request_prompt = await _prompt_for_request(session, facts)
                if existing_request_prompt is not None:
                    status, reason = _existing_request_outcome(existing_request_prompt, facts)
                    return _result_from_prompt(
                        status=status,
                        reason=reason,
                        mode=facts.mode,
                        facts=facts,
                        prompt=existing_request_prompt,
                        budget=budget,
                    )

                if facts.mode == "SIMULATION":
                    prompt = await _non_assistance_prompt(
                        session,
                        interview.id,
                        facts.event_id,
                        "Simulation keeps technical help disabled. Continue with your best "
                        "reasoning; CounterQ can clarify the problem statement if needed.",
                        self._clock(),
                    )
                    return _result_from_prompt(
                        status="REFUSED",
                        reason="SIMULATION_ASSISTANCE_PROHIBITED",
                        mode=facts.mode,
                        facts=facts,
                        prompt=prompt,
                        budget=budget,
                    )
                if not facts.meaningful_attempt_exists:
                    prompt = await _non_assistance_prompt(
                        session,
                        interview.id,
                        facts.event_id,
                        "Share the approach, code, or debugging step you have tried first.",
                        self._clock(),
                    )
                    return _result_from_prompt(
                        status="ATTEMPT_REQUIRED",
                        reason="MEANINGFUL_ATTEMPT_REQUIRED",
                        mode=facts.mode,
                        facts=facts,
                        prompt=prompt,
                        budget=budget,
                    )

                target = await _latest_gap_evidence(session, interview.id)
                prior = await _latest_delivered_assistance(
                    session,
                    interview.id,
                    target.concept_id if target else None,
                    target.skill_dimension_id if target else None,
                )
                if prior is not None and (
                    target is None or prior[1] is None or target.created_at <= prior[1]
                ):
                    return AssistanceRequestResult(
                        status="DEFERRED",
                        reason="STABLE_FAILURE_OR_PROGRESS_REQUIRED",
                        mode=facts.mode,
                        request_event_id=facts.event_id,
                        request_event_watermark=facts.watermark,
                        interviewer_prompt_id=None,
                        prompt_kind=None,
                        assistance_type=None,
                        hint_level=None,
                        target_concept_id=target.concept_id if target else None,
                        target_skill_dimension_id=target.skill_dimension_id if target else None,
                        source_code_snapshot_id=facts.source_code_snapshot_id,
                        invites_guided_retry=False,
                        budget=budget,
                    )

                timing = await InterviewRuntime(session, clock=self._clock).time_policy(
                    interview.id
                )
                if timing is None:
                    raise ValueError("Interview time policy is unavailable")
                decision = self._mode_policy.evaluate_assistance(
                    mode=facts.mode,
                    stage=interview.current_stage,
                    time_pressure=timing.pressure,
                    meaningful_attempt_exists=True,
                    gap_evidence_exists=target is not None,
                    highest_delivered_level=prior[0] if prior else None,
                )
                if not decision.allowed or decision.next_hint_level is None:
                    return AssistanceRequestResult(
                        status="DENIED",
                        reason=decision.reason,
                        mode=facts.mode,
                        request_event_id=facts.event_id,
                        request_event_watermark=facts.watermark,
                        interviewer_prompt_id=None,
                        prompt_kind=None,
                        assistance_type=None,
                        hint_level=None,
                        target_concept_id=target.concept_id if target else None,
                        target_skill_dimension_id=target.skill_dimension_id if target else None,
                        source_code_snapshot_id=facts.source_code_snapshot_id,
                        invites_guided_retry=False,
                        budget=budget,
                    )

                existing_authorized = await session.scalar(
                    select(InterviewerPrompt)
                    .where(
                        InterviewerPrompt.interview_session_id == interview.id,
                        InterviewerPrompt.assistance_type.is_not(None),
                        InterviewerPrompt.status == "AUTHORIZED",
                    )
                    .order_by(InterviewerPrompt.authorized_at.desc())
                    .limit(1)
                )
                if existing_authorized is not None:
                    return _result_from_prompt(
                        status="AUTHORIZED",
                        reason="OUTSTANDING_ASSISTANCE_ALREADY_AUTHORIZED",
                        mode=facts.mode,
                        facts=facts,
                        prompt=existing_authorized,
                        budget=budget,
                    )

                level = decision.next_hint_level
                invites_retry = level in {"STRUCTURAL_HINT", "DIRECT_TEACHING"}
                if not assistance_capacity_available(
                    budget, hint_level=level, invites_guided_retry=invites_retry
                ):
                    return AssistanceRequestResult(
                        status="DENIED",
                        reason="ASSISTANCE_BUDGET_EXHAUSTED_OR_RESERVED",
                        mode=facts.mode,
                        request_event_id=facts.event_id,
                        request_event_watermark=facts.watermark,
                        interviewer_prompt_id=None,
                        prompt_kind=None,
                        assistance_type=None,
                        hint_level=None,
                        target_concept_id=target.concept_id if target else None,
                        target_skill_dimension_id=target.skill_dimension_id if target else None,
                        source_code_snapshot_id=facts.source_code_snapshot_id,
                        invites_guided_retry=False,
                        budget=budget,
                    )
                prompt = await InterviewInteractionRepository(session).add_prompt(
                    interview_session_id=interview.id,
                    origin="SYSTEM",
                    kind="INSTRUCTION",
                    intent=_deterministic_assistance_text(level, target),
                    status="AUTHORIZED",
                    assistance_type=command.assistance_type or level,
                    hint_level=level,
                    assistance_trigger=command.trigger,
                    target_event_id=facts.event_id,
                    target_concept_id=target.concept_id if target else None,
                    target_skill_dimension_id=target.skill_dimension_id if target else None,
                    source_event_watermark=facts.watermark,
                    source_code_snapshot_id=facts.source_code_snapshot_id,
                    invites_guided_retry=invites_retry,
                    authorized_at=self._clock(),
                )
                updated_budget = await assistance_budget_snapshot(session, interview.id)
                assert updated_budget is not None
                return _result_from_prompt(
                    status="AUTHORIZED",
                    reason=decision.reason,
                    mode=facts.mode,
                    facts=facts,
                    prompt=prompt,
                    budget=updated_budget,
                )


async def _meaningful_attempt_exists(
    session: AsyncSession, session_id: UUID, *, before_sequence: int
) -> bool:
    response = await session.scalar(
        select(CandidateResponse.id)
        .join(
            CandidateResponseSource,
            CandidateResponseSource.candidate_response_id == CandidateResponse.id,
        )
        .join(
            InterviewEvent,
            InterviewEvent.id == CandidateResponseSource.interview_event_id,
        )
        .where(
            CandidateResponse.interview_session_id == session_id,
            CandidateResponse.ended_at.is_not(None),
            InterviewEvent.server_sequence < before_sequence,
        )
        .limit(1)
    )
    if response is not None:
        return True
    event = await session.scalar(
        select(InterviewEvent.id)
        .where(
            InterviewEvent.interview_session_id == session_id,
            InterviewEvent.server_sequence < before_sequence,
            InterviewEvent.event_type.in_(
                ("MEANINGFUL_CODE_CHANGE", "RUN_CLICKED", "TEST_COMPLETED")
            ),
        )
        .limit(1)
    )
    if event is not None:
        return True
    claim = await session.scalar(
        select(CandidateClaim.id)
        .join(InterviewEvent, InterviewEvent.id == CandidateClaim.source_event_id)
        .where(
            CandidateClaim.interview_session_id == session_id,
            CandidateClaim.status == "ACCEPTED_AS_INTERPRETATION",
            InterviewEvent.server_sequence < before_sequence,
        )
        .limit(1)
    )
    return claim is not None


async def _latest_gap_evidence(session: AsyncSession, session_id: UUID) -> _DiagnosticTarget | None:
    evidence = await session.scalar(
        select(Evidence)
        .where(
            Evidence.interview_session_id == session_id,
            Evidence.validation_status == "VALID",
            Evidence.invalidated_at.is_(None),
            Evidence.polarity.in_(("NEGATIVE", "MIXED")),
        )
        .order_by(Evidence.created_at.desc(), Evidence.id.desc())
        .limit(1)
    )
    if evidence is None:
        return None
    concept_id = await session.scalar(
        select(EvidenceConcept.concept_id)
        .where(EvidenceConcept.evidence_id == evidence.id)
        .order_by(EvidenceConcept.is_primary.desc())
        .limit(1)
    )
    skill_id = await session.scalar(
        select(EvidenceSkill.skill_dimension_id)
        .where(EvidenceSkill.evidence_id == evidence.id)
        .order_by(EvidenceSkill.is_primary.desc())
        .limit(1)
    )
    return _DiagnosticTarget(
        evidence_id=evidence.id,
        concept_id=cast(UUID | None, concept_id),
        skill_dimension_id=cast(UUID | None, skill_id),
        finding=evidence.finding,
        created_at=evidence.created_at,
    )


async def _latest_delivered_assistance(
    session: AsyncSession,
    session_id: UUID,
    concept_id: UUID | None,
    skill_id: UUID | None,
) -> tuple[str, datetime | None] | None:
    prompts = list(
        await session.scalars(
            select(InterviewerPrompt)
            .join(
                InterviewerPromptDelivery,
                InterviewerPromptDelivery.interviewer_prompt_id == InterviewerPrompt.id,
            )
            .where(
                InterviewerPrompt.interview_session_id == session_id,
                InterviewerPrompt.assistance_type.is_not(None),
                InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
                InterviewerPromptDelivery.delivery_state.in_(
                    ("DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED")
                ),
            )
        )
    )
    matching = [
        prompt
        for prompt in prompts
        if (concept_id is None or prompt.target_concept_id == concept_id)
        and (skill_id is None or prompt.target_skill_dimension_id == skill_id)
        and prompt.hint_level in HINT_LADDER
    ]
    if not matching:
        return None
    highest = max(matching, key=lambda item: HINT_LADDER.index(cast(str, item.hint_level)))
    completed_at = await session.scalar(
        select(InterviewerPromptDelivery.completed_at)
        .where(
            InterviewerPromptDelivery.interviewer_prompt_id == highest.id,
            InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
        )
        .order_by(InterviewerPromptDelivery.completed_at.desc().nullslast())
        .limit(1)
    )
    return cast(str, highest.hint_level), cast(datetime | None, completed_at)


async def _non_assistance_prompt(
    session: AsyncSession,
    session_id: UUID,
    request_event_id: UUID,
    text: str,
    authorized_at: datetime,
) -> InterviewerPrompt:
    return await InterviewInteractionRepository(session).add_prompt(
        interview_session_id=session_id,
        origin="SYSTEM",
        kind="CLARIFICATION",
        target_event_id=request_event_id,
        intent=text,
        status="AUTHORIZED",
        authorized_at=authorized_at,
    )


async def _prompt_for_request(
    session: AsyncSession, facts: _RequestFacts
) -> InterviewerPrompt | None:
    return cast(
        InterviewerPrompt | None,
        await session.scalar(
            select(InterviewerPrompt)
            .where(
                InterviewerPrompt.interview_session_id == facts.session_id,
                InterviewerPrompt.target_event_id == facts.event_id,
            )
            .order_by(InterviewerPrompt.created_at, InterviewerPrompt.id)
            .limit(1)
        ),
    )


def _existing_request_outcome(
    prompt: InterviewerPrompt, facts: _RequestFacts
) -> tuple[AssistanceRequestStatus, str]:
    if prompt.assistance_type is not None:
        return "AUTHORIZED", "IDEMPOTENT_ASSISTANCE_REQUEST"
    if facts.mode == "SIMULATION":
        return "REFUSED", "SIMULATION_ASSISTANCE_PROHIBITED"
    if not facts.meaningful_attempt_exists:
        return "ATTEMPT_REQUIRED", "MEANINGFUL_ATTEMPT_REQUIRED"
    return "DENIED", "IDEMPOTENT_NON_ASSISTANCE_REQUEST"


def _deterministic_assistance_text(level: str, target: _DiagnosticTarget | None) -> str:
    target_label = "your current approach" if target is None else "the uncertain invariant"
    return {
        "METACOGNITIVE": f"What part of {target_label} feels least certain, and why?",
        "PROBLEM_NARROWING": (
            f"Focus only on {target_label}. What must remain true after each step?"
        ),
        "CONCEPTUAL_HINT": (
            f"Revisit {target_label}: identify the invariant before changing the implementation."
        ),
        "STRUCTURAL_HINT": (
            f"Trace one concrete case for {target_label}, recording each state transition, "
            "then retry the smallest failing step."
        ),
        "DIRECT_TEACHING": (
            f"Make {target_label} explicit in the implementation, update dependent state in "
            "that invariant's order, then retry the failing case."
        ),
    }[level]


def _result_from_prompt(
    *,
    status: AssistanceRequestStatus,
    reason: str,
    mode: str,
    facts: _RequestFacts,
    prompt: InterviewerPrompt,
    budget: AssistanceBudgetSnapshot,
) -> AssistanceRequestResult:
    return AssistanceRequestResult(
        status=status,
        reason=reason,
        mode=mode,
        request_event_id=facts.event_id,
        request_event_watermark=facts.watermark,
        interviewer_prompt_id=prompt.id,
        prompt_kind=prompt.kind,
        assistance_type=prompt.assistance_type,
        hint_level=prompt.hint_level,
        target_concept_id=prompt.target_concept_id,
        target_skill_dimension_id=prompt.target_skill_dimension_id,
        source_code_snapshot_id=prompt.source_code_snapshot_id,
        invites_guided_retry=prompt.invites_guided_retry,
        budget=budget,
    )

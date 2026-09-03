"""Candidate-requested Coach assistance orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGatewayError
from app.ai_gateway.provider import ReasoningProviderError
from app.evidence.coordinator import (
    SessionEvaluationResult,
    SessionEvidenceEvaluationCoordinator,
)
from app.evidence.models import (
    Assessment,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.evidence.units import AssessmentInputBuilder, AssessmentUnit
from app.examiner.models import CandidateClaim
from app.interviews.assistance_facts import initial_final_defense_answer_captured
from app.interviews.assistance_policy import CoachAssistanceInput, relevant_reviewed_reference
from app.interviews.assistance_wording import CoachAssistanceWordingService
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
from app.observation.models import CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.models import Concept, InterviewPackVersion, ProblemVersion

AssistanceRequestStatus = Literal[
    "AUTHORIZED", "REFUSED", "ATTEMPT_REQUIRED", "DEFERRED", "DENIED"
]

_PENDING_INTENT = "[Coach assistance wording pending policy generation]"
_CANDIDATE_PROGRESS_SOURCES = ("CANDIDATE_VOICE", "NATIVE_EDITOR", "NATIVE_RUNNER")


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
    state_version: int
    stage: str
    mode: str
    candidate_level: str
    source_code_snapshot_id: UUID | None
    source_code_snapshot_version: int | None
    meaningful_attempt_exists: bool
    initial_final_defense_answer_captured: bool


@dataclass(frozen=True)
class _DiagnosticTarget:
    evidence_id: UUID
    concept_id: UUID | None
    concept_key: str | None
    skill_dimension_id: UUID | None
    skill_dimension_key: str | None
    finding: str
    boundary: str | None
    polarity: str
    strength: str
    confidence: Decimal
    source_watermark: int


@dataclass(frozen=True)
class _GenerationContext:
    prompt_id: UUID
    unit_key: str
    serialized_unit: str
    target: _DiagnosticTarget | None
    level: str
    assistance_type: str
    invites_guided_retry: bool
    wording_input: CoachAssistanceInput


class CoachAssistanceWorkflow:
    """Reserve, checkpoint, generate, revalidate, then authorize one intervention."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        evidence_coordinator: SessionEvidenceEvaluationCoordinator | None = None,
        wording_service: CoachAssistanceWordingService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._evidence_coordinator = evidence_coordinator
        self._wording_service = wording_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mode_policy = ModePolicy()

    async def request(self, command: AssistanceRequestCommand) -> AssistanceRequestResult:
        facts = await self._persist_request(command)
        reserved = await self._reserve_or_short_circuit(command, facts)
        if isinstance(reserved, AssistanceRequestResult):
            return reserved
        prompt_id = reserved
        try:
            assert self._evidence_coordinator is not None
            checkpoint = await self._evidence_coordinator.evaluate_active_checkpoint(
                facts.session_id
            )
            checkpoint_usable = bool(checkpoint.units) and all(
                unit.status == "COMPLETED" or unit.error_category == "ALREADY_EVALUATED"
                for unit in checkpoint.units
            )
            if checkpoint.failed_units or not checkpoint_usable:
                await self._terminalize(prompt_id, "REJECTED")
                return await self._result_without_prompt(
                    facts, "DEFERRED", "ACTIVE_EVIDENCE_CHECKPOINT_UNAVAILABLE"
                )
            context = await self._prepare_generation_context(
                command=command,
                facts=facts,
                prompt_id=prompt_id,
                checkpoint=checkpoint,
            )
            if isinstance(context, AssistanceRequestResult):
                return context
            assert self._wording_service is not None
            wording = await self._wording_service.generate(
                interview_session_id=facts.session_id,
                request_event_id=facts.event_id,
                wording_input=context.wording_input,
            )
            return await self._authorize_generated_wording(
                facts=facts,
                context=context,
                prompt_text=wording.prompt_text,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._terminalize(prompt_id, "CANCELLED"))
            raise
        except (AIGatewayError, ReasoningProviderError):
            await self._terminalize(prompt_id, "REJECTED")
            return await self._result_without_prompt(
                facts, "DEFERRED", "ASSISTANCE_WORDING_UNAVAILABLE"
            )
        except Exception:
            await self._terminalize(prompt_id, "REJECTED")
            raise

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
                existing = await session.scalar(
                    select(InterviewEvent).where(
                        InterviewEvent.interview_session_id == interview.id,
                        InterviewEvent.idempotency_key == command.idempotency_key,
                    )
                )
                if existing is not None:
                    if (
                        existing.event_type != "CANDIDATE_ASSISTANCE_REQUESTED"
                        or existing.source != "SYSTEM"
                        or existing.payload.get("trigger") != command.trigger
                    ):
                        raise ValueError(
                            "Idempotency key conflicts with existing accepted event"
                        )
                    return _request_facts_from_event(existing)
                latest_snapshot = await _latest_snapshot(session, interview.id)
                next_sequence = interview.last_server_sequence + 1
                attempt = await _meaningful_attempt_exists(
                    session, interview.id, before_sequence=next_sequence
                )
                defense_answer = await initial_final_defense_answer_captured(
                    session, interview.id, before_sequence=next_sequence
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
                            "captured_state_version": interview.state_version,
                            "captured_stage": interview.current_stage,
                            "captured_mode": configuration.mode,
                            "captured_candidate_level": configuration.level,
                            "captured_snapshot_version": (
                                latest_snapshot.version_number if latest_snapshot else None
                            ),
                            "meaningful_attempt_exists": attempt,
                            "initial_final_defense_answer_captured": defense_answer,
                        },
                        provenance={"mode_policy_version": self._mode_policy.policy_version},
                        schema_version="candidate.assistance-request.v1",
                        expected_state_version=interview.state_version,
                        code_snapshot_id=latest_snapshot.id if latest_snapshot else None,
                    )
                )
                return _request_facts_from_event(accepted.event)

    async def _reserve_or_short_circuit(
        self, command: AssistanceRequestCommand, facts: _RequestFacts
    ) -> UUID | AssistanceRequestResult:
        async with self._sessionmaker() as session:
            async with session.begin():
                interview = await _lock_session(session, facts.session_id)
                budget = await _required_budget(session, interview.id, for_update=True)
                existing_request = await _prompt_for_request(session, facts)
                if existing_request is not None:
                    return _existing_result(existing_request, facts, budget)

                configuration = await session.get(
                    InterviewConfiguration, interview.interview_configuration_id
                )
                latest_snapshot = await _latest_snapshot(session, interview.id)
                if interview.status != "ACTIVE":
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "INTERVIEW_NO_LONGER_ACTIVE", budget
                    )
                if (
                    configuration is None
                    or configuration.mode != facts.mode
                    or configuration.level != facts.candidate_level
                ):
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "INTERVIEW_CONFIGURATION_CHANGED", budget
                    )
                if (
                    interview.state_version != facts.state_version
                    or interview.current_stage != facts.stage
                ):
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "INTERVIEW_STATE_CHANGED", budget
                    )
                if (
                    (latest_snapshot.id if latest_snapshot else None)
                    != facts.source_code_snapshot_id
                    or (latest_snapshot.version_number if latest_snapshot else None)
                    != facts.source_code_snapshot_version
                ):
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "CANDIDATE_CODE_CHANGED", budget
                    )
                if await _candidate_progress_after(session, interview.id, facts.watermark):
                    return _result_without_prompt_snapshot(
                        facts,
                        "DEFERRED",
                        "CANDIDATE_PROGRESS_SUPERSEDED_ASSISTANCE",
                        budget,
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
                        facts=facts,
                        prompt=prompt,
                        budget=budget,
                    )

                timing = await InterviewRuntime(session, clock=self._clock).time_policy(
                    interview.id
                )
                if timing is None:
                    raise ValueError("Interview time policy is unavailable")
                decision = self._mode_policy.evaluate_assistance(
                    mode=facts.mode,
                    stage=facts.stage,
                    time_pressure=timing.pressure,
                    meaningful_attempt_exists=True,
                    gap_evidence_exists=False,
                    highest_delivered_level=None,
                    initial_final_defense_answer_captured=(
                        facts.initial_final_defense_answer_captured
                    ),
                )
                if not decision.allowed:
                    return _result_without_prompt_snapshot(
                        facts, "DENIED", decision.reason, budget
                    )
                outstanding = await _outstanding_assistance_prompt(session, interview.id)
                if outstanding is not None:
                    if outstanding.status == "AUTHORIZED":
                        return _result_from_prompt(
                            status="AUTHORIZED",
                            reason="OUTSTANDING_ASSISTANCE_ALREADY_AUTHORIZED",
                            facts=facts,
                            prompt=outstanding,
                            budget=budget,
                        )
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "ASSISTANCE_GENERATION_IN_PROGRESS", budget
                    )
                if not assistance_capacity_available(
                    budget, hint_level="METACOGNITIVE", invites_guided_retry=False
                ):
                    return _result_without_prompt_snapshot(
                        facts,
                        "DENIED",
                        "ASSISTANCE_BUDGET_EXHAUSTED_OR_RESERVED",
                        budget,
                    )
                if self._evidence_coordinator is None or self._wording_service is None:
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "ASSISTANCE_PROVIDER_UNAVAILABLE", budget
                    )
                prompt = await InterviewInteractionRepository(session).add_prompt(
                    interview_session_id=interview.id,
                    origin="SYSTEM",
                    kind="INSTRUCTION",
                    intent=_PENDING_INTENT,
                    status="PROPOSED",
                    assistance_type="METACOGNITIVE",
                    hint_level="METACOGNITIVE",
                    assistance_trigger=command.trigger,
                    target_event_id=facts.event_id,
                    source_event_watermark=facts.watermark,
                    source_code_snapshot_id=facts.source_code_snapshot_id,
                    invites_guided_retry=False,
                )
                return prompt.id

    async def _prepare_generation_context(
        self,
        *,
        command: AssistanceRequestCommand,
        facts: _RequestFacts,
        prompt_id: UUID,
        checkpoint: SessionEvaluationResult,
    ) -> _GenerationContext | AssistanceRequestResult:
        del command  # Candidate preference never selects software authorization.
        checkpoint_unit = checkpoint.units[0]
        evidence_ids = checkpoint_unit.evidence_ids
        async with self._sessionmaker() as session:
            async with session.begin():
                prompt = await _lock_prompt(session, prompt_id)
                rejection = await self._revalidation_outcome(
                    session=session,
                    facts=facts,
                    prompt=prompt,
                    target=None,
                    unit_key=checkpoint_unit.unit_key,
                    serialized_unit=None,
                )
                if rejection is not None:
                    return await self._reject_in_transaction(
                        session, facts, prompt, *rejection
                    )
                target = await _select_checkpoint_target(
                    session, facts.session_id, evidence_ids
                )
                any_prior = await _any_delivered_assistance(session, facts.session_id)
                prior_level, same_target_without_new_failure = await _causal_prior_level(
                    session, facts.session_id, target
                )
                if facts.stage == "FINAL_DEFENSE":
                    # Final Defense starts a fresh independent diagnostic attempt.
                    prior_level = None
                    same_target_without_new_failure = False
                if target is None and any_prior:
                    prompt.status = "CANCELLED"
                    budget = await _required_budget(session, facts.session_id)
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "PROGRESS_RESTORED_NO_CURRENT_GAP", budget
                    )
                if same_target_without_new_failure:
                    prompt.status = "CANCELLED"
                    budget = await _required_budget(session, facts.session_id)
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "STABLE_FAILURE_OR_PROGRESS_REQUIRED", budget
                    )

                interview = await _lock_session(session, facts.session_id)
                timing = await InterviewRuntime(session, clock=self._clock).time_policy(
                    interview.id
                )
                assert timing is not None
                decision = self._mode_policy.evaluate_assistance(
                    mode=facts.mode,
                    stage=facts.stage,
                    time_pressure=timing.pressure,
                    meaningful_attempt_exists=True,
                    gap_evidence_exists=target is not None,
                    highest_delivered_level=prior_level,
                    initial_final_defense_answer_captured=(
                        facts.initial_final_defense_answer_captured
                    ),
                )
                if not decision.allowed or decision.next_hint_level is None:
                    prompt.status = (
                        "EXPIRED"
                        if "PRESSURE" in decision.reason or "WRAP" in decision.reason
                        else "REJECTED"
                    )
                    budget = await _required_budget(session, facts.session_id)
                    return _result_without_prompt_snapshot(
                        facts, "DENIED", decision.reason, budget
                    )
                level = decision.next_hint_level
                assistance_type = _select_assistance_type(
                    level=level, unit_kind=checkpoint_unit.unit_kind, stage=facts.stage
                )
                invites_retry = level in {"STRUCTURAL_HINT", "DIRECT_TEACHING"}
                available = await _required_budget(
                    session,
                    facts.session_id,
                    for_update=True,
                    exclude_prompt_id=prompt.id,
                )
                if not assistance_capacity_available(
                    available,
                    hint_level=level,
                    invites_guided_retry=invites_retry,
                ):
                    prompt.status = "REJECTED"
                    return _result_without_prompt_snapshot(
                        facts,
                        "DENIED",
                        "ASSISTANCE_BUDGET_EXHAUSTED_OR_RESERVED",
                        available,
                    )
                unit = await _unit_for_key(session, facts.session_id, checkpoint_unit.unit_key)
                if unit is None:
                    prompt.status = "STALE"
                    budget = await _required_budget(session, facts.session_id)
                    return _result_without_prompt_snapshot(
                        facts, "DEFERRED", "ACTIVE_ASSESSMENT_UNIT_CHANGED", budget
                    )
                wording_input = await _wording_input(
                    session=session,
                    facts=facts,
                    unit=unit,
                    target=target,
                    level=level,
                    assistance_type=assistance_type,
                )
                prompt.hint_level = level
                prompt.assistance_type = assistance_type
                prompt.target_concept_id = target.concept_id if target else None
                prompt.target_skill_dimension_id = (
                    target.skill_dimension_id if target else None
                )
                prompt.invites_guided_retry = invites_retry
                await session.flush()
                return _GenerationContext(
                    prompt_id=prompt.id,
                    unit_key=unit.unit_key,
                    serialized_unit=unit.serialize(),
                    target=target,
                    level=level,
                    assistance_type=assistance_type,
                    invites_guided_retry=invites_retry,
                    wording_input=wording_input,
                )

    async def _authorize_generated_wording(
        self,
        *,
        facts: _RequestFacts,
        context: _GenerationContext,
        prompt_text: str,
    ) -> AssistanceRequestResult:
        async with self._sessionmaker() as session:
            async with session.begin():
                prompt = await _lock_prompt(session, context.prompt_id)
                rejection = await self._revalidation_outcome(
                    session=session,
                    facts=facts,
                    prompt=prompt,
                    target=context.target,
                    unit_key=context.unit_key,
                    serialized_unit=context.serialized_unit,
                )
                if rejection is not None:
                    return await self._reject_in_transaction(
                        session, facts, prompt, *rejection
                    )
                budget = await _required_budget(
                    session,
                    facts.session_id,
                    for_update=True,
                    exclude_prompt_id=prompt.id,
                )
                if not assistance_capacity_available(
                    budget,
                    hint_level=context.level,
                    invites_guided_retry=context.invites_guided_retry,
                ):
                    prompt.status = "REJECTED"
                    return _result_without_prompt_snapshot(
                        facts,
                        "DENIED",
                        "ASSISTANCE_BUDGET_EXHAUSTED_OR_RESERVED",
                        budget,
                    )
                prompt.intent = prompt_text
                prompt.status = "AUTHORIZED"
                prompt.authorized_at = self._clock()
                await session.flush()
                updated = await _required_budget(session, facts.session_id)
                return _result_from_prompt(
                    status="AUTHORIZED",
                    reason="ASSISTANCE_ALLOWED",
                    facts=facts,
                    prompt=prompt,
                    budget=updated,
                )

    async def _revalidation_outcome(
        self,
        *,
        session: AsyncSession,
        facts: _RequestFacts,
        prompt: InterviewerPrompt,
        target: _DiagnosticTarget | None,
        unit_key: str,
        serialized_unit: str | None,
    ) -> tuple[AssistanceRequestStatus, str, str] | None:
        interview = await _lock_session(session, facts.session_id)
        if prompt.status != "PROPOSED":
            return ("DEFERRED", "ASSISTANCE_RESERVATION_NO_LONGER_PROPOSED", "CANCELLED")
        if interview.status != "ACTIVE":
            return ("DEFERRED", "INTERVIEW_NO_LONGER_ACTIVE", "STALE")
        if self._clock() >= interview.deadline_at:
            return ("DENIED", "INTERVIEW_DEADLINE_REACHED", "EXPIRED")
        configuration = await session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        if (
            configuration is None
            or configuration.mode != facts.mode
            or configuration.level != facts.candidate_level
        ):
            return ("DEFERRED", "INTERVIEW_CONFIGURATION_CHANGED", "STALE")
        if interview.state_version != facts.state_version or interview.current_stage != facts.stage:
            return ("DEFERRED", "INTERVIEW_STATE_CHANGED", "STALE")
        request_event = await session.get(InterviewEvent, facts.event_id)
        if (
            request_event is None
            or request_event.interview_session_id != facts.session_id
            or request_event.event_type != "CANDIDATE_ASSISTANCE_REQUESTED"
            or request_event.server_sequence != facts.watermark
            or request_event.code_snapshot_id != facts.source_code_snapshot_id
        ):
            return ("DEFERRED", "ASSISTANCE_REQUEST_PROVENANCE_CHANGED", "STALE")
        timing = await InterviewRuntime(session, clock=self._clock).time_policy(interview.id)
        if timing is None or timing.pressure in {"DEFENSE_RESERVED", "WRAP_ONLY"}:
            return ("DENIED", "PROTECTED_CLOSEOUT_TIME", "EXPIRED")
        if facts.stage == "FINAL_DEFENSE" and not await initial_final_defense_answer_captured(
            session, facts.session_id, before_sequence=facts.watermark
        ):
            return ("DENIED", "FINAL_DEFENSE_INITIAL_ANSWER_REQUIRED", "STALE")
        if await _candidate_progress_after(session, facts.session_id, facts.watermark):
            return ("DEFERRED", "CANDIDATE_PROGRESS_SUPERSEDED_ASSISTANCE", "STALE")
        snapshot = await _latest_snapshot(session, facts.session_id)
        if (snapshot.id if snapshot else None) != facts.source_code_snapshot_id or (
            snapshot.version_number if snapshot else None
        ) != facts.source_code_snapshot_version:
            return ("DEFERRED", "CANDIDATE_CODE_CHANGED", "STALE")
        if target is not None and not await _target_is_active(session, target):
            return ("DEFERRED", "DIAGNOSTIC_TARGET_NO_LONGER_ACTIVE", "STALE")
        unit = await _unit_for_key(session, facts.session_id, unit_key)
        if unit is None or (
            serialized_unit is not None and unit.serialize() != serialized_unit
        ):
            return ("DEFERRED", "ACTIVE_ASSESSMENT_UNIT_CHANGED", "STALE")
        other = await _outstanding_assistance_prompt(
            session, facts.session_id, exclude_prompt_id=prompt.id
        )
        if other is not None:
            return ("DEFERRED", "ASSISTANCE_SLOT_NO_LONGER_AVAILABLE", "CANCELLED")
        return None

    async def _reject_in_transaction(
        self,
        session: AsyncSession,
        facts: _RequestFacts,
        prompt: InterviewerPrompt,
        status: AssistanceRequestStatus,
        reason: str,
        prompt_status: str,
    ) -> AssistanceRequestResult:
        prompt.status = prompt_status
        await session.flush()
        budget = await _required_budget(session, facts.session_id)
        return _result_without_prompt_snapshot(facts, status, reason, budget)

    async def _terminalize(self, prompt_id: UUID, status: str) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                prompt = await session.get(InterviewerPrompt, prompt_id)
                if prompt is not None and prompt.status == "PROPOSED":
                    prompt.status = status

    async def _result_without_prompt(
        self,
        facts: _RequestFacts,
        status: AssistanceRequestStatus,
        reason: str,
    ) -> AssistanceRequestResult:
        async with self._sessionmaker() as session:
            budget = await _required_budget(session, facts.session_id)
            return _result_without_prompt_snapshot(facts, status, reason, budget)


def _request_facts_from_event(event: InterviewEvent) -> _RequestFacts:
    payload = event.payload
    required = {
        "captured_state_version",
        "captured_stage",
        "captured_mode",
        "captured_candidate_level",
        "meaningful_attempt_exists",
        "initial_final_defense_answer_captured",
    }
    if not required.issubset(payload):
        raise ValueError("Persisted assistance request provenance is incomplete")
    return _RequestFacts(
        session_id=event.interview_session_id,
        event_id=event.id,
        watermark=event.server_sequence,
        state_version=cast(int, payload["captured_state_version"]),
        stage=str(payload["captured_stage"]),
        mode=str(payload["captured_mode"]),
        candidate_level=str(payload["captured_candidate_level"]),
        source_code_snapshot_id=event.code_snapshot_id,
        source_code_snapshot_version=cast(
            int | None, payload.get("captured_snapshot_version")
        ),
        meaningful_attempt_exists=bool(payload["meaningful_attempt_exists"]),
        initial_final_defense_answer_captured=bool(
            payload["initial_final_defense_answer_captured"]
        ),
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
        .join(InterviewEvent, InterviewEvent.id == CandidateResponseSource.interview_event_id)
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


async def _select_checkpoint_target(
    session: AsyncSession, session_id: UUID, evidence_ids: tuple[UUID, ...]
) -> _DiagnosticTarget | None:
    if not evidence_ids:
        return None
    rows = list(
        await session.scalars(
            select(Evidence).where(
                Evidence.interview_session_id == session_id,
                Evidence.id.in_(evidence_ids),
                Evidence.validation_status == "VALID",
                Evidence.invalidated_at.is_(None),
                Evidence.polarity.in_(("NEGATIVE", "MIXED")),
            )
        )
    )
    ranked = sorted(
        rows,
        key=lambda item: (
            {"NEGATIVE": 0, "MIXED": 1}[item.polarity],
            {"STRONG": 0, "MODERATE": 1, "WEAK": 2}[item.strength],
            -item.confidence,
            str(item.id),
        ),
    )
    if not ranked:
        return None
    evidence = ranked[0]
    concept_row = (
        await session.execute(
            select(Concept.id, Concept.canonical_key)
            .join(EvidenceConcept, EvidenceConcept.concept_id == Concept.id)
            .where(EvidenceConcept.evidence_id == evidence.id)
            .order_by(EvidenceConcept.is_primary.desc(), Concept.canonical_key)
            .limit(1)
        )
    ).first()
    skill_row = (
        await session.execute(
            select(SkillDimension.id, SkillDimension.canonical_key)
            .join(EvidenceSkill, EvidenceSkill.skill_dimension_id == SkillDimension.id)
            .where(EvidenceSkill.evidence_id == evidence.id)
            .order_by(EvidenceSkill.is_primary.desc(), SkillDimension.canonical_key)
            .limit(1)
        )
    ).first()
    source_watermark = await _evidence_source_watermark(session, evidence.id)
    boundary = await session.scalar(
        select(Breakpoint.breakpoint_key)
        .join(BreakpointEvidence, BreakpointEvidence.breakpoint_id == Breakpoint.id)
        .where(BreakpointEvidence.evidence_id == evidence.id)
        .order_by(BreakpointEvidence.created_at.desc())
        .limit(1)
    )
    if source_watermark is None:
        return None
    return _DiagnosticTarget(
        evidence_id=evidence.id,
        concept_id=cast(UUID | None, concept_row[0] if concept_row else None),
        concept_key=str(concept_row[1]) if concept_row else None,
        skill_dimension_id=cast(UUID | None, skill_row[0] if skill_row else None),
        skill_dimension_key=str(skill_row[1]) if skill_row else None,
        finding=evidence.finding,
        boundary=cast(str | None, boundary),
        polarity=evidence.polarity,
        strength=evidence.strength,
        confidence=evidence.confidence,
        source_watermark=source_watermark,
    )


async def _causal_prior_level(
    session: AsyncSession, session_id: UUID, target: _DiagnosticTarget | None
) -> tuple[str | None, bool]:
    if target is None:
        return None, False
    prompts = list(
        await session.scalars(
            select(InterviewerPrompt).where(
                InterviewerPrompt.interview_session_id == session_id,
                InterviewerPrompt.assistance_type.is_not(None),
                InterviewerPrompt.hint_level.in_(HINT_LADDER),
            )
        )
    )
    causal: list[str] = []
    matched_without_new_failure = False
    for prompt in prompts:
        delivery_watermark = await _delivery_watermark(session, prompt.id)
        if delivery_watermark is None:
            continue
        exact_target = (
            prompt.target_concept_id == target.concept_id
            and prompt.target_skill_dimension_id == target.skill_dimension_id
            and (target.concept_id is not None or target.skill_dimension_id is not None)
        )
        broad_direct_response = (
            prompt.target_concept_id is None
            and prompt.target_skill_dimension_id is None
            and await _evidence_directly_answers_prompt(session, target.evidence_id, prompt.id)
        )
        if not (exact_target or broad_direct_response):
            continue
        if target.source_watermark > delivery_watermark:
            causal.append(cast(str, prompt.hint_level))
        else:
            matched_without_new_failure = True
    if not causal:
        return None, matched_without_new_failure
    return max(causal, key=lambda level: HINT_LADDER.index(cast(str, level))), False


async def _delivery_watermark(session: AsyncSession, prompt_id: UUID) -> int | None:
    value = await session.scalar(
        select(func.max(InterviewEvent.server_sequence))
        .select_from(InterviewerPromptDelivery)
        .join(
            TranscriptSegment,
            TranscriptSegment.id == InterviewerPromptDelivery.actual_transcript_segment_id,
        )
        .join(InterviewEvent, InterviewEvent.id == TranscriptSegment.interview_event_id)
        .where(
            InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
            InterviewerPromptDelivery.delivery_state.in_(
                ("DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED")
            ),
        )
    )
    return int(value) if value is not None else None


async def _evidence_directly_answers_prompt(
    session: AsyncSession, evidence_id: UUID, prompt_id: UUID
) -> bool:
    return (
        await session.scalar(
            select(CandidateResponse.id)
            .join(Assessment, Assessment.candidate_response_id == CandidateResponse.id)
            .join(Evidence, Evidence.originating_assessment_id == Assessment.id)
            .where(
                Evidence.id == evidence_id,
                CandidateResponse.interviewer_prompt_id == prompt_id,
            )
            .limit(1)
        )
        is not None
    )


async def _any_delivered_assistance(session: AsyncSession, session_id: UUID) -> bool:
    return (
        await session.scalar(
            select(InterviewerPromptDelivery.id)
            .join(
                InterviewerPrompt,
                InterviewerPrompt.id == InterviewerPromptDelivery.interviewer_prompt_id,
            )
            .where(
                InterviewerPrompt.interview_session_id == session_id,
                InterviewerPrompt.assistance_type.is_not(None),
                InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
                InterviewerPromptDelivery.delivery_state.in_(
                    ("DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED")
                ),
            )
            .limit(1)
        )
        is not None
    )


async def _wording_input(
    *,
    session: AsyncSession,
    facts: _RequestFacts,
    unit: AssessmentUnit,
    target: _DiagnosticTarget | None,
    level: str,
    assistance_type: str,
) -> CoachAssistanceInput:
    interview = await session.get(InterviewSession, facts.session_id)
    assert interview is not None
    problem = await session.get(ProblemVersion, interview.problem_version_id)
    pack = await session.get(InterviewPackVersion, interview.interview_pack_version_id)
    if problem is None or pack is None or pack.review_status != "REVIEWED":
        raise ValueError("Coach wording requires the exact reviewed problem and pack")
    candidate_context = unit.input_payload.get("assessment_unit", {})
    if not isinstance(candidate_context, dict):
        candidate_context = {}
    else:
        candidate_context = dict(candidate_context)
    if facts.source_code_snapshot_id is not None:
        snapshot = await session.get(CodeSnapshot, facts.source_code_snapshot_id)
        if (
            snapshot is None
            or snapshot.interview_session_id != facts.session_id
            or snapshot.version_number != facts.source_code_snapshot_version
        ):
            raise ValueError("Captured Coach code snapshot is unavailable or changed")
        candidate_context["current_code_snapshot"] = {
            "id": str(snapshot.id),
            "version_number": snapshot.version_number,
            "language": snapshot.language,
            "content_hash": snapshot.content_hash,
            "source_code": snapshot.source_code,
        }
    return CoachAssistanceInput(
        selected_hint_level=level,
        assistance_type=assistance_type,
        stage=facts.stage,
        mode=facts.mode,
        candidate_level=facts.candidate_level,
        target_concept_key=target.concept_key if target else None,
        target_skill_dimension_key=target.skill_dimension_key if target else None,
        evidence_finding=target.finding if target else None,
        evidence_boundary=target.boundary if target else None,
        problem={
            "id": str(problem.id),
            "title": problem.title,
            "statement": problem.statement,
            "constraints": problem.constraints_json,
            "examples": problem.examples_json,
        },
        reviewed_technical_reference=relevant_reviewed_reference(
            pack.pack_json, target_concept_key=target.concept_key if target else None
        ),
        candidate_context=cast(dict[str, object], candidate_context),
    )


def _select_assistance_type(*, level: str, unit_kind: str, stage: str) -> str:
    if stage == "TESTING_DEBUGGING" or unit_kind == "EXECUTION_DEBUGGING":
        return "DEBUGGING_HINT"
    return level


async def _unit_for_key(
    session: AsyncSession, session_id: UUID, unit_key: str
) -> AssessmentUnit | None:
    units = await AssessmentInputBuilder(session).build_active_checkpoint(session_id)
    return next((unit for unit in units if unit.unit_key == unit_key), None)


async def _target_is_active(session: AsyncSession, target: _DiagnosticTarget) -> bool:
    evidence = await session.get(Evidence, target.evidence_id)
    return bool(
        evidence is not None
        and evidence.validation_status == "VALID"
        and evidence.invalidated_at is None
        and evidence.polarity in {"NEGATIVE", "MIXED"}
        and await _evidence_source_watermark(session, evidence.id) == target.source_watermark
    )


async def _evidence_source_watermark(session: AsyncSession, evidence_id: UUID) -> int | None:
    value = await session.scalar(
        select(func.max(InterviewEvent.server_sequence))
        .join(EvidenceSource, EvidenceSource.interview_event_id == InterviewEvent.id)
        .where(EvidenceSource.evidence_id == evidence_id)
    )
    return int(value) if value is not None else None


async def _candidate_progress_after(
    session: AsyncSession, session_id: UUID, watermark: int
) -> bool:
    return (
        await session.scalar(
            select(InterviewEvent.id)
            .where(
                InterviewEvent.interview_session_id == session_id,
                InterviewEvent.server_sequence > watermark,
                InterviewEvent.source.in_(_CANDIDATE_PROGRESS_SOURCES),
            )
            .limit(1)
        )
        is not None
    )


async def _latest_snapshot(session: AsyncSession, session_id: UUID) -> CodeSnapshot | None:
    return cast(
        CodeSnapshot | None,
        await session.scalar(
            select(CodeSnapshot)
            .where(CodeSnapshot.interview_session_id == session_id)
            .order_by(CodeSnapshot.version_number.desc())
            .limit(1)
        ),
    )


async def _lock_session(session: AsyncSession, session_id: UUID) -> InterviewSession:
    interview = await session.scalar(
        select(InterviewSession).where(InterviewSession.id == session_id).with_for_update()
    )
    if interview is None:
        raise ValueError("InterviewSession was not found")
    return interview


async def _lock_prompt(session: AsyncSession, prompt_id: UUID) -> InterviewerPrompt:
    prompt = await session.scalar(
        select(InterviewerPrompt).where(InterviewerPrompt.id == prompt_id).with_for_update()
    )
    if prompt is None:
        raise ValueError("Assistance reservation was not found")
    return prompt


async def _required_budget(
    session: AsyncSession,
    session_id: UUID,
    *,
    for_update: bool = False,
    exclude_prompt_id: UUID | None = None,
) -> AssistanceBudgetSnapshot:
    budget = await assistance_budget_snapshot(
        session,
        session_id,
        for_update=for_update,
        exclude_prompt_id=exclude_prompt_id,
    )
    if budget is None:
        raise ValueError("SessionBudget was not found")
    return budget


async def _outstanding_assistance_prompt(
    session: AsyncSession,
    session_id: UUID,
    *,
    exclude_prompt_id: UUID | None = None,
) -> InterviewerPrompt | None:
    statement = (
        select(InterviewerPrompt)
        .where(
            InterviewerPrompt.interview_session_id == session_id,
            InterviewerPrompt.assistance_type.is_not(None),
            InterviewerPrompt.status.in_(("PROPOSED", "AUTHORIZED")),
        )
        .order_by(InterviewerPrompt.created_at, InterviewerPrompt.id)
        .limit(1)
    )
    if exclude_prompt_id is not None:
        statement = statement.where(InterviewerPrompt.id != exclude_prompt_id)
    return cast(InterviewerPrompt | None, await session.scalar(statement))


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


def _existing_result(
    prompt: InterviewerPrompt,
    facts: _RequestFacts,
    budget: AssistanceBudgetSnapshot,
) -> AssistanceRequestResult:
    if prompt.assistance_type is None:
        if facts.mode == "SIMULATION":
            status: AssistanceRequestStatus = "REFUSED"
            reason = "SIMULATION_ASSISTANCE_PROHIBITED"
        elif not facts.meaningful_attempt_exists:
            status = "ATTEMPT_REQUIRED"
            reason = "MEANINGFUL_ATTEMPT_REQUIRED"
        else:
            status = "DENIED"
            reason = "IDEMPOTENT_NON_ASSISTANCE_REQUEST"
        return _result_from_prompt(
            status=status, reason=reason, facts=facts, prompt=prompt, budget=budget
        )
    if prompt.status == "AUTHORIZED":
        return _result_from_prompt(
            status="AUTHORIZED",
            reason="IDEMPOTENT_ASSISTANCE_REQUEST",
            facts=facts,
            prompt=prompt,
            budget=budget,
        )
    reason = (
        "ASSISTANCE_GENERATION_IN_PROGRESS"
        if prompt.status == "PROPOSED"
        else "IDEMPOTENT_ASSISTANCE_TERMINATED"
    )
    return _result_without_prompt_snapshot(facts, "DEFERRED", reason, budget)


def _result_without_prompt_snapshot(
    facts: _RequestFacts,
    status: AssistanceRequestStatus,
    reason: str,
    budget: AssistanceBudgetSnapshot,
) -> AssistanceRequestResult:
    return AssistanceRequestResult(
        status=status,
        reason=reason,
        mode=facts.mode,
        request_event_id=facts.event_id,
        request_event_watermark=facts.watermark,
        interviewer_prompt_id=None,
        prompt_kind=None,
        assistance_type=None,
        hint_level=None,
        target_concept_id=None,
        target_skill_dimension_id=None,
        source_code_snapshot_id=facts.source_code_snapshot_id,
        invites_guided_retry=False,
        budget=budget,
    )


def _result_from_prompt(
    *,
    status: AssistanceRequestStatus,
    reason: str,
    facts: _RequestFacts,
    prompt: InterviewerPrompt,
    budget: AssistanceBudgetSnapshot,
) -> AssistanceRequestResult:
    return AssistanceRequestResult(
        status=status,
        reason=reason,
        mode=facts.mode,
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

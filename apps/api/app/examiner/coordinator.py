from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import (
    AIGateway,
    AIGatewayResult,
    ReasoningBudgetExceeded,
    StructuredOutputValidationFailure,
    get_or_create_policy_version,
)
from app.ai_gateway.models import AIInvocation
from app.ai_gateway.provider import ReasoningProvider, ReasoningProviderError
from app.config.settings import Settings
from app.examiner.analysis_schema import (
    ExaminerAnalysisResult,
    ExaminerDecisionOutput,
    ExaminerVerificationReason,
)
from app.examiner.context import (
    ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS,
    ExaminerContext,
    ExaminerContextBuilder,
)
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.examiner.policy import (
    LIVE_EXAMINER_EXPIRY_POLICY,
    LIVE_EXAMINER_INSTRUCTIONS,
    live_examiner_policy_descriptor,
)
from app.examiner.reasoning_pipeline import (
    STRONG_ESCALATION_MIN_REMAINING_SECONDS,
    ExaminerReasoningTier,
    build_reasoning_input_payload,
    initial_reasoning_tier,
    next_strong_verification_reason,
    reasoning_route_for_tier,
    unresolved_consequential_challenge,
)
from app.examiner.repository import ExaminerRepository
from app.interviews.models import InterviewSession
from app.observation.models import InterviewEvent
from app.observation.repository import ObservationRepository

LiveExaminerStatus = Literal[
    "NO_ELIGIBLE_OBSERVATION",
    "REUSED",
    "PROPOSED",
    "STALE",
    "SUPPRESSED",
    "CANCELLED",
    "ERROR",
]

logger = structlog.get_logger(__name__)


class LiveExaminerError(Exception):
    category = "LIVE_EXAMINER_ERROR"

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class LiveExaminerNoEligibleObservation(LiveExaminerError):
    category = "NO_ELIGIBLE_OBSERVATION"


class LiveExaminerSuperseded(LiveExaminerError):
    category = "SUPERSEDED"


@dataclass(frozen=True)
class LiveExaminerClaimDebug:
    id: UUID
    normalized_claim: str
    claim_type: str
    verbatim_excerpt: str | None
    confidence: float


@dataclass(frozen=True)
class LiveExaminerDecisionDebug:
    id: UUID
    action: str
    target_kind: str
    target_claim_id: UUID | None
    target_code_snapshot_id: UUID | None
    proposed_probe_strategy: str | None
    technical_rationale: str
    confidence: float | None
    priority: int | None
    urgency: int | None
    status: str
    policy_gate_outcome: str | None
    policy_gate_reason: str | None
    deadline_at: datetime | None
    target_ranking: dict[str, str] | None = None
    verification: dict[str, object] | None = None


@dataclass(frozen=True)
class LiveExaminerDebugResult:
    status: LiveExaminerStatus
    source_kind: str | None
    source_event_id: UUID | None
    source_event_watermark: int | None
    source_state_version: int | None
    code_snapshot_id: UUID | None
    code_snapshot_version: int | None
    ai_invocation_id: UUID | None
    provider: str | None
    model: str | None
    latency_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal | None
    currency: str | None
    claims: list[LiveExaminerClaimDebug]
    decision: LiveExaminerDecisionDebug | None
    message: str | None = None
    reasoning_tier: ExaminerReasoningTier | None = None
    preliminary_ai_invocation_id: UUID | None = None


@dataclass
class _ActiveExaminerTask:
    source_event_id: UUID
    task: asyncio.Task[LiveExaminerDebugResult]
    cancelled_by_event_id: UUID | None = None


@dataclass
class LiveExaminerTaskRegistry:
    active: dict[UUID, _ActiveExaminerTask] = field(default_factory=dict)


_DEFAULT_REGISTRY = LiveExaminerTaskRegistry()


def get_live_examiner_task_registry() -> LiveExaminerTaskRegistry:
    return _DEFAULT_REGISTRY


class LiveExaminerCoordinator:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: ReasoningProvider,
        registry: LiveExaminerTaskRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._provider = provider
        self._registry = registry or get_live_examiner_task_registry()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def notify_new_observation(
        self,
        *,
        interview_session_id: UUID,
        source_event_id: UUID,
    ) -> None:
        await self._cancel_active(interview_session_id, cancelled_by_event_id=source_event_id)
        if self._settings.live_examiner_autostart:
            self.submit(interview_session_id=interview_session_id, source_event_id=source_event_id)

    def submit(
        self,
        *,
        interview_session_id: UUID,
        source_event_id: UUID,
    ) -> asyncio.Task[LiveExaminerDebugResult]:
        existing = self._registry.active.get(interview_session_id)
        if existing is not None and not existing.task.done():
            existing.cancelled_by_event_id = source_event_id
            existing.task.cancel()
        task = asyncio.create_task(
            self._run_analysis(
                interview_session_id=interview_session_id,
                source_event_id=source_event_id,
            )
        )
        self._registry.active[interview_session_id] = _ActiveExaminerTask(
            source_event_id=source_event_id,
            task=task,
        )
        task.add_done_callback(
            lambda completed: self._clear_if_current(interview_session_id, completed)
        )
        return task

    async def analyze_latest(self, interview_session_id: UUID) -> LiveExaminerDebugResult:
        async with self._sessionmaker() as session:
            source_event_id = await ExaminerContextBuilder(session).latest_eligible_event_id(
                interview_session_id
            )
        if source_event_id is None:
            return LiveExaminerDebugResult(
                status="NO_ELIGIBLE_OBSERVATION",
                source_kind=None,
                source_event_id=None,
                source_event_watermark=None,
                source_state_version=None,
                code_snapshot_id=None,
                code_snapshot_version=None,
                ai_invocation_id=None,
                provider=None,
                model=None,
                latency_ms=None,
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                estimated_cost=None,
                currency=None,
                claims=[],
                decision=None,
                message="No eligible finalized transcript or meaningful code change was found.",
            )
        return await self.submit(
            interview_session_id=interview_session_id,
            source_event_id=source_event_id,
        )

    async def _run_analysis(
        self,
        *,
        interview_session_id: UUID,
        source_event_id: UUID,
    ) -> LiveExaminerDebugResult:
        started = time.perf_counter()
        outcome = "ERROR"
        try:
            result = await self._execute_analysis(
                interview_session_id=interview_session_id,
                source_event_id=source_event_id,
            )
            outcome = result.status
            return result
        except asyncio.CancelledError:
            outcome = "CANCELLED"
            raise
        finally:
            logger.info(
                "live_examiner_total_timing",
                interview_session_id=str(interview_session_id),
                source_event_id=str(source_event_id),
                total_examiner_elapsed_ms=max(
                    int((time.perf_counter() - started) * 1000),
                    0,
                ),
                outcome=outcome,
            )

    async def _execute_analysis(
        self,
        *,
        interview_session_id: UUID,
        source_event_id: UUID,
    ) -> LiveExaminerDebugResult:
        deadline_at = self._clock() + timedelta(
            seconds=self._settings.live_examiner_usefulness_seconds
        )
        context_started = time.perf_counter()
        context = await self._build_context(source_event_id)
        context_build_ms = max(int((time.perf_counter() - context_started) * 1000), 0)
        lookup_started = time.perf_counter()
        existing = await self._existing_result(context)
        existing_result_lookup_ms = max(
            int((time.perf_counter() - lookup_started) * 1000),
            0,
        )
        logger.info(
            "live_examiner_preparation_timing",
            interview_session_id=str(interview_session_id),
            source_event_id=str(source_event_id),
            context_build_ms=context_build_ms,
            existing_result_lookup_ms=existing_result_lookup_ms,
            usefulness_remaining_ms=max(
                int((deadline_at - self._clock()).total_seconds() * 1000),
                0,
            ),
            existing_result_found=existing is not None,
        )
        if existing is not None:
            return existing
        if self._clock() >= deadline_at:
            raise LiveExaminerError("Live Examiner usefulness deadline expired before dispatch")

        gateway = AIGateway(
            settings=self._settings,
            sessionmaker=self._sessionmaker,
            provider=self._provider,
        )
        tier = initial_reasoning_tier(context.context_json)
        try:
            result = await self._reason(
                gateway=gateway,
                context=context,
                deadline_at=deadline_at,
                tier=tier,
            )
        except StructuredOutputValidationFailure:
            return self._structured_output_error_result(context=context, tier=tier)
        except ReasoningProviderError as exc:
            if exc.category == "STRUCTURED_OUTPUT_INVALID":
                return self._structured_output_error_result(context=context, tier=tier)
            if exc.category == "TIMEOUT":
                return await self._timeout_result(context=context, tier=tier)
            raise
        preliminary_invocation_id: UUID | None = None
        required_verification_reason = next_strong_verification_reason(tier, result.parsed)
        if required_verification_reason is not None:
            preliminary_invocation_id = result.invocation_id
            pre_escalation_status = await self._revalidate(context, deadline_at)
            if pre_escalation_status != "PROPOSED":
                return self._unpersisted_result(
                    context=context,
                    result=result,
                    status="STALE",
                    tier=tier,
                    preliminary_invocation_id=preliminary_invocation_id,
                    message="Source became stale before strong verification.",
                )
            remaining = (deadline_at - self._clock()).total_seconds()
            if remaining < STRONG_ESCALATION_MIN_REMAINING_SECONDS:
                return self._unpersisted_result(
                    context=context,
                    result=result,
                    status="SUPPRESSED",
                    tier=tier,
                    preliminary_invocation_id=preliminary_invocation_id,
                    message="Insufficient usefulness window for required strong verification.",
                )
            try:
                result = await self._reason(
                    gateway=gateway,
                    context=context,
                    deadline_at=deadline_at,
                    tier="STRONG",
                    preliminary_ai_invocation_id=preliminary_invocation_id,
                    required_verification_reason=required_verification_reason,
                    preliminary_analysis=result.parsed,
                )
            except StructuredOutputValidationFailure:
                return self._structured_output_error_result(
                    context=context,
                    tier="STRONG",
                    preliminary_invocation_id=preliminary_invocation_id,
                )
            except ReasoningProviderError as exc:
                if exc.category == "STRUCTURED_OUTPUT_INVALID":
                    return self._structured_output_error_result(
                        context=context,
                        tier="STRONG",
                        preliminary_invocation_id=preliminary_invocation_id,
                    )
                if exc.category == "TIMEOUT":
                    return await self._timeout_result(
                        context=context,
                        tier="STRONG",
                        preliminary_invocation_id=preliminary_invocation_id,
                    )
                raise
            except ReasoningBudgetExceeded:
                return self._unpersisted_result(
                    context=context,
                    result=result,
                    status="SUPPRESSED",
                    tier=tier,
                    preliminary_invocation_id=preliminary_invocation_id,
                    message="Strong reasoning budget is unavailable; unsafe challenge suppressed.",
                )
            tier = "STRONG"
            if unresolved_consequential_challenge(result.parsed):
                return self._unpersisted_result(
                    context=context,
                    result=result,
                    status="SUPPRESSED",
                    tier=tier,
                    preliminary_invocation_id=preliminary_invocation_id,
                    message="Strong verification left a consequential challenge unresolved.",
                )

        admitted_status = await self._revalidate(context, deadline_at)
        if tier == "STRONG" and admitted_status != "PROPOSED":
            return self._unpersisted_result(
                context=context,
                result=result,
                status="STALE",
                tier=tier,
                preliminary_invocation_id=preliminary_invocation_id,
                message="Source became stale during strong verification.",
            )
        persisted = await self._persist_result(
            context=context,
            analysis=result.parsed,
            ai_invocation_id=result.invocation_id,
            ai_policy_version_id=result.policy_version_id,
            deadline_at=deadline_at,
            status=admitted_status,
        )
        return LiveExaminerDebugResult(
            status=cast(LiveExaminerStatus, admitted_status),
            source_kind=context.observation.kind,
            source_event_id=context.observation.source_event_id,
            source_event_watermark=context.observation.source_event_watermark,
            source_state_version=context.observation.interview_state_version,
            code_snapshot_id=context.observation.code_snapshot_id
            or context.observation.associated_code_snapshot_id,
            code_snapshot_version=context.observation.code_snapshot_version
            or context.observation.associated_code_snapshot_version,
            ai_invocation_id=result.invocation_id,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.usage.input_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost=result.estimated_cost,
            currency=result.currency,
            claims=persisted[0],
            decision=persisted[1],
            reasoning_tier=tier,
            preliminary_ai_invocation_id=preliminary_invocation_id,
        )

    async def _reason(
        self,
        *,
        gateway: AIGateway,
        context: ExaminerContext,
        deadline_at: datetime,
        tier: ExaminerReasoningTier,
        preliminary_ai_invocation_id: UUID | None = None,
        required_verification_reason: ExaminerVerificationReason | None = None,
        preliminary_analysis: ExaminerAnalysisResult | None = None,
    ) -> AIGatewayResult[ExaminerAnalysisResult]:
        remaining = max(0.1, (deadline_at - self._clock()).total_seconds())
        metadata: dict[str, object] = {
            "source_event_id": str(context.observation.source_event_id),
            "source_event_watermark": context.observation.source_event_watermark,
            "reasoning_tier": tier,
        }
        if preliminary_ai_invocation_id is not None:
            metadata["preliminary_ai_invocation_id"] = str(preliminary_ai_invocation_id)
        if required_verification_reason is not None:
            metadata["required_verification_reason"] = required_verification_reason
        route = reasoning_route_for_tier(
            tier,
            standard_effort=self._settings.reasoning_standard_effort,
            strong_effort=self._settings.reasoning_strong_effort,
        )
        input_payload = build_reasoning_input_payload(
            context_json=context.context_json,
            tier=tier,
            required_verification_reason=required_verification_reason,
            preliminary_analysis=preliminary_analysis,
        )
        return await gateway.reason_structured(
            interview_session_id=context.observation.interview_session_id,
            capability=route.capability,
            purpose=route.purpose,
            policy=live_examiner_policy_descriptor(),
            instructions=LIVE_EXAMINER_INSTRUCTIONS,
            input_content=json.dumps(input_payload, sort_keys=True, default=str),
            output_model=ExaminerAnalysisResult,
            timeout_seconds=remaining,
            usefulness_deadline=deadline_at,
            reasoning_effort_override=route.reasoning_effort,
            correlation_id=str(context.observation.source_event_id),
            metadata=metadata,
        )

    def _unpersisted_result(
        self,
        *,
        context: ExaminerContext,
        result: AIGatewayResult[ExaminerAnalysisResult],
        status: LiveExaminerStatus,
        tier: ExaminerReasoningTier,
        preliminary_invocation_id: UUID | None,
        message: str,
    ) -> LiveExaminerDebugResult:
        return LiveExaminerDebugResult(
            status=status,
            source_kind=context.observation.kind,
            source_event_id=context.observation.source_event_id,
            source_event_watermark=context.observation.source_event_watermark,
            source_state_version=context.observation.interview_state_version,
            code_snapshot_id=context.observation.code_snapshot_id
            or context.observation.associated_code_snapshot_id,
            code_snapshot_version=context.observation.code_snapshot_version
            or context.observation.associated_code_snapshot_version,
            ai_invocation_id=result.invocation_id,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.usage.input_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost=result.estimated_cost,
            currency=result.currency,
            claims=[],
            decision=None,
            message=message,
            reasoning_tier=tier,
            preliminary_ai_invocation_id=preliminary_invocation_id,
        )

    def _structured_output_error_result(
        self,
        *,
        context: ExaminerContext,
        tier: ExaminerReasoningTier,
        preliminary_invocation_id: UUID | None = None,
    ) -> LiveExaminerDebugResult:
        return LiveExaminerDebugResult(
            status="ERROR",
            source_kind=context.observation.kind,
            source_event_id=context.observation.source_event_id,
            source_event_watermark=context.observation.source_event_watermark,
            source_state_version=context.observation.interview_state_version,
            code_snapshot_id=context.observation.code_snapshot_id
            or context.observation.associated_code_snapshot_id,
            code_snapshot_version=context.observation.code_snapshot_version
            or context.observation.associated_code_snapshot_version,
            ai_invocation_id=None,
            provider=None,
            model=None,
            latency_ms=None,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            estimated_cost=None,
            currency=None,
            claims=[],
            decision=None,
            message=(
                "Live Examiner returned invalid structured output; no recommendation "
                "was persisted."
            ),
            reasoning_tier=tier,
            preliminary_ai_invocation_id=preliminary_invocation_id,
        )

    async def _timeout_result(
        self,
        *,
        context: ExaminerContext,
        tier: ExaminerReasoningTier,
        preliminary_invocation_id: UUID | None = None,
    ) -> LiveExaminerDebugResult:
        route = reasoning_route_for_tier(
            tier,
            standard_effort=self._settings.reasoning_standard_effort,
            strong_effort=self._settings.reasoning_strong_effort,
        )
        async with self._sessionmaker() as session:
            invocation = await session.scalar(
                select(AIInvocation)
                .where(
                    AIInvocation.interview_session_id
                    == context.observation.interview_session_id
                )
                .where(AIInvocation.purpose == route.purpose)
                .where(AIInvocation.capability == route.capability)
                .where(AIInvocation.status == "TIMED_OUT")
                .where(AIInvocation.error_class == "TIMEOUT")
                .order_by(AIInvocation.started_at.desc(), AIInvocation.created_at.desc())
                .limit(1)
            )
        latency_ms = None
        if invocation is not None and invocation.completed_at is not None:
            latency_ms = max(
                int((invocation.completed_at - invocation.started_at).total_seconds() * 1000),
                0,
            )
        return LiveExaminerDebugResult(
            status="SUPPRESSED",
            source_kind=context.observation.kind,
            source_event_id=context.observation.source_event_id,
            source_event_watermark=context.observation.source_event_watermark,
            source_state_version=context.observation.interview_state_version,
            code_snapshot_id=context.observation.code_snapshot_id
            or context.observation.associated_code_snapshot_id,
            code_snapshot_version=context.observation.code_snapshot_version
            or context.observation.associated_code_snapshot_version,
            ai_invocation_id=invocation.id if invocation is not None else None,
            provider=invocation.provider if invocation is not None else None,
            model=invocation.model if invocation is not None else None,
            latency_ms=latency_ms,
            input_tokens=invocation.input_tokens if invocation is not None else None,
            cached_input_tokens=(
                invocation.cached_input_tokens if invocation is not None else None
            ),
            output_tokens=invocation.output_tokens if invocation is not None else None,
            estimated_cost=invocation.estimated_cost if invocation is not None else None,
            currency=invocation.currency if invocation is not None else None,
            claims=[],
            decision=None,
            message=(
                "Live Examiner exceeded the usefulness window; no recommendation "
                "was delivered."
            ),
            reasoning_tier=tier,
            preliminary_ai_invocation_id=preliminary_invocation_id,
        )

    async def _build_context(self, source_event_id: UUID) -> ExaminerContext:
        async with self._sessionmaker() as session:
            return await ExaminerContextBuilder(session).build_for_event(source_event_id)

    async def _existing_result(
        self,
        context: ExaminerContext,
    ) -> LiveExaminerDebugResult | None:
        async with self._sessionmaker() as session:
            policy_version = await get_or_create_policy_version(
                session, live_examiner_policy_descriptor()
            )
            decision = await session.scalar(
                select(ExaminerDecision)
                .where(
                    ExaminerDecision.interview_session_id
                    == context.observation.interview_session_id
                )
                .where(ExaminerDecision.target_event_id == context.observation.source_event_id)
                .where(ExaminerDecision.ai_policy_version_id == policy_version.id)
                .order_by(ExaminerDecision.created_at.desc())
                .limit(1)
            )
            if decision is None:
                return None
            claims = list(
                await session.scalars(
                    select(CandidateClaim).where(
                        CandidateClaim.ai_invocation_id == decision.ai_invocation_id
                    )
                )
            )
            invocation = await session.get(AIInvocation, decision.ai_invocation_id)
            return _debug_result_from_existing(context, decision, claims, invocation)

    async def _revalidate(self, context: ExaminerContext, deadline_at: datetime) -> str:
        if self._clock() >= deadline_at:
            return "STALE"
        active = self._registry.active.get(context.observation.interview_session_id)
        if active is not None and active.source_event_id != context.observation.source_event_id:
            return "STALE"
        async with self._sessionmaker() as session:
            interview = await session.get(
                InterviewSession,
                context.observation.interview_session_id,
            )
            source_event = await session.get(InterviewEvent, context.observation.source_event_id)
            if interview is None or source_event is None:
                return "STALE"
            if interview.status != "ACTIVE":
                return "STALE"
            if interview.state_version != context.observation.interview_state_version:
                return "STALE"
            if source_event.interview_session_id != interview.id:
                return "STALE"
            newer_candidate_event = await session.scalar(
                select(InterviewEvent.id)
                .where(InterviewEvent.interview_session_id == interview.id)
                .where(
                    InterviewEvent.server_sequence
                    > context.observation.source_event_watermark
                )
                .where(InterviewEvent.source.in_(["CANDIDATE_VOICE", "NATIVE_EDITOR"]))
                .limit(1)
            )
            if newer_candidate_event is not None:
                return "STALE"
            if context.observation.kind == "CODE_MEANINGFULLY_CHANGED":
                latest = await ObservationRepository(session).latest_code_snapshot(interview.id)
                if (
                    latest is not None
                    and context.observation.code_snapshot_version is not None
                    and latest.version_number > context.observation.code_snapshot_version
                ):
                    return "STALE"
        return "PROPOSED"

    async def _persist_result(
        self,
        *,
        context: ExaminerContext,
        analysis: ExaminerAnalysisResult,
        ai_invocation_id: UUID,
        ai_policy_version_id: UUID,
        deadline_at: datetime,
        status: str,
    ) -> tuple[list[LiveExaminerClaimDebug], LiveExaminerDecisionDebug]:
        async with self._sessionmaker() as session:
            async with session.begin():
                repository = ExaminerRepository(session)
                claims: list[CandidateClaim] = []
                if status == "PROPOSED":
                    for output_claim in analysis.claims:
                        claims.append(
                            await repository.add_candidate_claim(
                                interview_session_id=context.observation.interview_session_id,
                                origin_kind=_claim_origin_kind(context.observation.kind),
                                normalized_claim=output_claim.normalized_claim,
                                claim_type=output_claim.claim_type,
                                extraction_confidence=Decimal(str(output_claim.confidence)),
                                status="ACCEPTED_AS_INTERPRETATION",
                                ai_invocation_id=ai_invocation_id,
                                ai_policy_version_id=ai_policy_version_id,
                                source_transcript_segment_id=(
                                    context.observation.transcript_segment_id
                                ),
                                source_event_id=context.observation.source_event_id,
                                source_code_snapshot_id=(
                                    context.observation.code_snapshot_id
                                    or context.observation.associated_code_snapshot_id
                                ),
                                source_code_diff_id=context.observation.code_diff_id,
                                verbatim_excerpt=output_claim.verbatim_excerpt,
                            )
                        )

                decision_output = analysis.decision
                target_claim = (
                    claims[decision_output.target_claim_index]
                    if status == "PROPOSED"
                    and decision_output.target_kind == "CLAIM"
                    and decision_output.target_claim_index is not None
                    else None
                )
                decision = await repository.add_examiner_decision(
                    interview_session_id=context.observation.interview_session_id,
                    action=decision_output.action,
                    target_claim_id=target_claim.id if target_claim else None,
                    target_event_id=context.observation.source_event_id,
                    target_code_snapshot_id=(
                        context.observation.code_snapshot_id
                        or (
                            context.observation.associated_code_snapshot_id
                            if decision_output.target_kind == "CODE_SNAPSHOT"
                            else None
                        )
                    ),
                    proposed_probe_strategy=decision_output.proposed_probe_strategy,
                    technical_rationale=decision_output.technical_rationale,
                    confidence=Decimal(str(decision_output.confidence)),
                    priority=decision_output.priority,
                    urgency=decision_output.urgency,
                    source_event_watermark=context.observation.source_event_watermark,
                    source_state_version=context.observation.interview_state_version,
                    deadline_at=deadline_at,
                    expiry_policy=LIVE_EXAMINER_EXPIRY_POLICY,
                    policy_gate_outcome=None,
                    policy_gate_reason=None,
                    status=status,
                    ai_invocation_id=ai_invocation_id,
                    ai_policy_version_id=ai_policy_version_id,
                )
                return (
                    [_claim_debug(claim) for claim in claims],
                    _decision_debug(
                        decision,
                        decision_output.target_kind,
                        decision_output,
                    ),
                )

    async def _cancel_active(
        self,
        interview_session_id: UUID,
        *,
        cancelled_by_event_id: UUID,
    ) -> None:
        active = self._registry.active.get(interview_session_id)
        if active is not None and not active.task.done():
            active.cancelled_by_event_id = cancelled_by_event_id
            active.task.cancel()

    def _clear_if_current(
        self,
        interview_session_id: UUID,
        completed: asyncio.Task[LiveExaminerDebugResult],
    ) -> None:
        active = self._registry.active.get(interview_session_id)
        if active is not None and active.task is completed:
            self._registry.active.pop(interview_session_id, None)


def observation_is_live_examiner_eligible(kind: str | None) -> bool:
    return kind in ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS


def _claim_origin_kind(observation_kind: str) -> str:
    if observation_kind == "CANDIDATE_TRANSCRIPT_FINALIZED":
        return "TRANSCRIPT"
    if observation_kind == "CODE_MEANINGFULLY_CHANGED":
        return "CODE"
    return "MULTIMODAL_CONTEXT"


def _claim_debug(claim: CandidateClaim) -> LiveExaminerClaimDebug:
    return LiveExaminerClaimDebug(
        id=claim.id,
        normalized_claim=claim.normalized_claim,
        claim_type=claim.claim_type,
        verbatim_excerpt=claim.verbatim_excerpt,
        confidence=float(claim.extraction_confidence),
    )


def _decision_debug(
    decision: ExaminerDecision,
    target_kind: str,
    output: ExaminerDecisionOutput | None = None,
) -> LiveExaminerDecisionDebug:
    return LiveExaminerDecisionDebug(
        id=decision.id,
        action=decision.action,
        target_kind=target_kind,
        target_claim_id=decision.target_claim_id,
        target_code_snapshot_id=decision.target_code_snapshot_id,
        proposed_probe_strategy=decision.proposed_probe_strategy,
        technical_rationale=decision.technical_rationale,
        confidence=float(decision.confidence) if decision.confidence is not None else None,
        priority=decision.priority,
        urgency=decision.urgency,
        status=decision.status,
        policy_gate_outcome=decision.policy_gate_outcome,
        policy_gate_reason=decision.policy_gate_reason,
        deadline_at=decision.deadline_at,
        target_ranking=(
            cast(dict[str, str], output.target_ranking.model_dump(mode="json"))
            if output
            else None
        ),
        verification=(output.verification.model_dump(mode="json") if output else None),
    )


def _debug_result_from_existing(
    context: ExaminerContext,
    decision: ExaminerDecision,
    claims: list[CandidateClaim],
    invocation: AIInvocation | None,
) -> LiveExaminerDebugResult:
    return LiveExaminerDebugResult(
        status="REUSED",
        source_kind=context.observation.kind,
        source_event_id=context.observation.source_event_id,
        source_event_watermark=context.observation.source_event_watermark,
        source_state_version=context.observation.interview_state_version,
        code_snapshot_id=context.observation.code_snapshot_id
        or context.observation.associated_code_snapshot_id,
        code_snapshot_version=context.observation.code_snapshot_version
        or context.observation.associated_code_snapshot_version,
        ai_invocation_id=decision.ai_invocation_id,
        provider=invocation.provider if invocation else None,
        model=invocation.model if invocation else None,
        latency_ms=invocation.latency_ms if invocation else None,
        input_tokens=invocation.input_tokens if invocation else None,
        cached_input_tokens=invocation.cached_input_tokens if invocation else None,
        output_tokens=invocation.output_tokens if invocation else None,
        estimated_cost=invocation.estimated_cost if invocation else None,
        currency=invocation.currency if invocation else None,
        claims=[_claim_debug(claim) for claim in claims],
        decision=_decision_debug(decision, _target_kind_from_decision(decision)),
    )


def _target_kind_from_decision(decision: ExaminerDecision) -> str:
    if decision.target_claim_id is not None:
        return "CLAIM"
    if decision.target_code_snapshot_id is not None:
        return "CODE_SNAPSHOT"
    if decision.target_event_id is not None:
        return "EVENT"
    return "NONE"

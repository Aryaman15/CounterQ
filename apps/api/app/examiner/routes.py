from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.ai_gateway.gateway import StructuredOutputValidationFailure
from app.ai_gateway.provider import ReasoningProvider, ReasoningProviderError
from app.ai_gateway.routes import get_reasoning_provider_builder
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_sessionmaker
from app.examiner.coordinator import (
    LiveExaminerCoordinator,
    LiveExaminerDebugResult,
    LiveExaminerError,
)
from app.examiner.development_workflow import (
    DevelopmentAnalyzeAndAuthorizeResult,
    DevelopmentAnalyzeAndAuthorizeWorkflow,
)
from app.interviews.prompt_authorization import PromptAuthorizationError

router = APIRouter(prefix="/api/examiner", tags=["examiner"])


class DevelopmentAnalyzeLatestRequest(BaseModel):
    interview_session_id: UUID


class DevelopmentExaminerClaim(BaseModel):
    id: UUID
    normalized_claim: str
    claim_type: str
    verbatim_excerpt: str | None
    confidence: float


class DevelopmentExaminerDecision(BaseModel):
    id: UUID
    action: Literal["WAIT", "OBSERVE", "ASK", "PROBE"]
    target_kind: Literal["NONE", "CLAIM", "EVENT", "CODE_SNAPSHOT"]
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
    deadline_at: str | None
    target_ranking: dict[str, str] | None
    verification: dict[str, object] | None


class DevelopmentAnalyzeLatestResponse(BaseModel):
    status: str
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
    claims: list[DevelopmentExaminerClaim]
    decision: DevelopmentExaminerDecision | None
    message: str | None


class DevelopmentPolicyGateResult(BaseModel):
    decision_id: UUID
    disposition: str
    decision_status: str
    policy_gate_outcome: str | None
    reason: str
    interviewer_prompt_id: UUID | None
    prompt_kind: str | None
    probe_strategy: str | None
    candidate_safe_text: str | None


class DevelopmentPolicyGateTiming(BaseModel):
    analysis_completed_at: str
    gate_evaluated_at: str | None
    decision_deadline_at: str | None
    remaining_usefulness_seconds_at_analysis: float | None
    remaining_usefulness_seconds_at_gate: float | None
    authorized_at: str | None
    delivery_window_expires_at: str | None
    delivery_window_seconds: float
    delivery_window_state: str | None


class DevelopmentAnalyzeAndAuthorizeResponse(BaseModel):
    analysis: DevelopmentAnalyzeLatestResponse
    policy_gate: DevelopmentPolicyGateResult | None
    timing: DevelopmentPolicyGateTiming


def build_live_examiner_coordinator(
    settings: Settings,
    provider_builder: Callable[[Settings], ReasoningProvider],
) -> LiveExaminerCoordinator:
    return LiveExaminerCoordinator(
        settings=settings,
        sessionmaker=get_sessionmaker(),
        provider=provider_builder(settings),
    )


def get_live_examiner_coordinator_builder() -> Callable[
    [Settings, Callable[[Settings], ReasoningProvider]], LiveExaminerCoordinator
]:
    return build_live_examiner_coordinator


@router.post(
    "/development-analyze-latest",
    response_model=DevelopmentAnalyzeLatestResponse,
)
async def development_analyze_latest(
    request: DevelopmentAnalyzeLatestRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], ReasoningProvider],
        Depends(get_reasoning_provider_builder),
    ],
    coordinator_builder: Annotated[
        Callable[[Settings, Callable[[Settings], ReasoningProvider]], LiveExaminerCoordinator],
        Depends(get_live_examiner_coordinator_builder),
    ],
) -> DevelopmentAnalyzeLatestResponse:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Live Examiner development analysis is local-development only",
            },
        )

    coordinator = coordinator_builder(settings, provider_builder)
    try:
        result = await coordinator.analyze_latest(request.interview_session_id)
    except StructuredOutputValidationFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "category": exc.category,
                "message": (
                    "Examiner returned an invalid structured decision. No decision was persisted."
                ),
                "retryable": False,
            },
        ) from exc
    except ReasoningProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except LiveExaminerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc

    return _response_from_result(result)


@router.post(
    "/development-analyze-and-authorize",
    response_model=DevelopmentAnalyzeAndAuthorizeResponse,
)
async def development_analyze_and_authorize(
    request: DevelopmentAnalyzeLatestRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], ReasoningProvider],
        Depends(get_reasoning_provider_builder),
    ],
    coordinator_builder: Annotated[
        Callable[[Settings, Callable[[Settings], ReasoningProvider]], LiveExaminerCoordinator],
        Depends(get_live_examiner_coordinator_builder),
    ],
) -> DevelopmentAnalyzeAndAuthorizeResponse:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Live Examiner analyze-and-authorize is local-development only",
            },
        )

    coordinator = coordinator_builder(settings, provider_builder)
    workflow = DevelopmentAnalyzeAndAuthorizeWorkflow(
        coordinator=coordinator,
        sessionmaker=get_sessionmaker(),
        authorized_prompt_delivery_window_seconds=(
            settings.authorized_prompt_delivery_window_seconds
        ),
    )
    try:
        result = await workflow.analyze_and_authorize_latest(request.interview_session_id)
    except StructuredOutputValidationFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "category": exc.category,
                "message": (
                    "Examiner returned an invalid structured decision. No decision was persisted."
                ),
                "retryable": False,
            },
        ) from exc
    except ReasoningProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc
    except PromptAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": "PROMPT_AUTHORIZATION", "message": str(exc)},
        ) from exc
    except LiveExaminerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc

    return _combined_response_from_result(result)


def _response_from_result(result: LiveExaminerDebugResult) -> DevelopmentAnalyzeLatestResponse:
    return DevelopmentAnalyzeLatestResponse(
        status=result.status,
        source_kind=result.source_kind,
        source_event_id=result.source_event_id,
        source_event_watermark=result.source_event_watermark,
        source_state_version=result.source_state_version,
        code_snapshot_id=result.code_snapshot_id,
        code_snapshot_version=result.code_snapshot_version,
        ai_invocation_id=result.ai_invocation_id,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        cached_input_tokens=result.cached_input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        currency=result.currency,
        claims=[
            DevelopmentExaminerClaim(
                id=claim.id,
                normalized_claim=claim.normalized_claim,
                claim_type=claim.claim_type,
                verbatim_excerpt=claim.verbatim_excerpt,
                confidence=claim.confidence,
            )
            for claim in result.claims
        ],
        decision=(
            DevelopmentExaminerDecision(
                id=result.decision.id,
                action=result.decision.action,  # type: ignore[arg-type]
                target_kind=result.decision.target_kind,  # type: ignore[arg-type]
                target_claim_id=result.decision.target_claim_id,
                target_code_snapshot_id=result.decision.target_code_snapshot_id,
                proposed_probe_strategy=result.decision.proposed_probe_strategy,
                technical_rationale=result.decision.technical_rationale,
                confidence=result.decision.confidence,
                priority=result.decision.priority,
                urgency=result.decision.urgency,
                status=result.decision.status,
                policy_gate_outcome=result.decision.policy_gate_outcome,
                policy_gate_reason=result.decision.policy_gate_reason,
                deadline_at=(
                    result.decision.deadline_at.isoformat()
                    if result.decision.deadline_at
                    else None
                ),
                target_ranking=result.decision.target_ranking,
                verification=result.decision.verification,
            )
            if result.decision
            else None
        ),
        message=result.message,
    )


def _combined_response_from_result(
    result: DevelopmentAnalyzeAndAuthorizeResult,
) -> DevelopmentAnalyzeAndAuthorizeResponse:
    timing = result.timing
    return DevelopmentAnalyzeAndAuthorizeResponse(
        analysis=_response_from_result(result.analysis),
        policy_gate=(
            DevelopmentPolicyGateResult(
                decision_id=result.policy_gate.decision_id,
                disposition=result.policy_gate.disposition,
                decision_status=result.policy_gate.decision_status,
                policy_gate_outcome=result.policy_gate.policy_gate_outcome,
                reason=result.policy_gate.reason,
                interviewer_prompt_id=result.policy_gate.prompt_id,
                prompt_kind=result.policy_gate.prompt_kind,
                probe_strategy=result.policy_gate.probe_strategy,
                candidate_safe_text=result.policy_gate.candidate_safe_text,
            )
            if result.policy_gate
            else None
        ),
        timing=DevelopmentPolicyGateTiming(
            analysis_completed_at=timing.analysis_completed_at.isoformat(),
            gate_evaluated_at=(
                timing.gate_evaluated_at.isoformat() if timing.gate_evaluated_at else None
            ),
            decision_deadline_at=(
                timing.decision_deadline_at.isoformat() if timing.decision_deadline_at else None
            ),
            remaining_usefulness_seconds_at_analysis=(
                timing.remaining_usefulness_seconds_at_analysis
            ),
            remaining_usefulness_seconds_at_gate=timing.remaining_usefulness_seconds_at_gate,
            authorized_at=timing.authorized_at.isoformat() if timing.authorized_at else None,
            delivery_window_expires_at=(
                timing.delivery_window_expires_at.isoformat()
                if timing.delivery_window_expires_at
                else None
            ),
            delivery_window_seconds=timing.delivery_window_seconds,
            delivery_window_state=timing.delivery_window_state,
        ),
    )

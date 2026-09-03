from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider_factory import (
    ReasoningProviderConfigurationError,
    build_reasoning_provider,
)
from app.config.settings import Settings, get_settings
from app.db.session import get_sessionmaker
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.interviews.assistance import (
    AssistanceRequestCommand,
    AssistanceRequestResult,
    CoachAssistanceWorkflow,
)
from app.interviews.mode_policy import ModePolicy
from app.interviews.runtime import InterviewRuntimeError

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


class CandidateAssistanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class AssistanceBudgetResponse(BaseModel):
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


class CandidateAssistanceResponse(BaseModel):
    status: Literal["AUTHORIZED", "REFUSED", "ATTEMPT_REQUIRED", "DEFERRED", "DENIED"]
    reason: str
    mode: Literal["COACH", "SIMULATION"]
    mode_policy_version: Literal["mode-policy.v1"]
    request_event_id: UUID
    request_event_watermark: int
    interviewer_prompt_id: UUID | None
    prompt_kind: Literal["CLARIFICATION", "INSTRUCTION"] | None
    assistance_type: (
        Literal[
            "METACOGNITIVE",
            "PROBLEM_NARROWING",
            "CONCEPTUAL_HINT",
            "STRUCTURAL_HINT",
            "DIRECT_TEACHING",
            "DEBUGGING_HINT",
            "CORRECTNESS_FEEDBACK",
        ]
        | None
    )
    hint_level: (
        Literal[
            "METACOGNITIVE",
            "PROBLEM_NARROWING",
            "CONCEPTUAL_HINT",
            "STRUCTURAL_HINT",
            "DIRECT_TEACHING",
        ]
        | None
    )
    target_concept_id: UUID | None
    target_skill_dimension_id: UUID | None
    source_code_snapshot_id: UUID | None
    invites_guided_retry: bool
    budget: AssistanceBudgetResponse


@router.post(
    "/{interview_session_id}/assistance-requests",
    response_model=CandidateAssistanceResponse,
)
async def request_candidate_assistance(
    interview_session_id: UUID,
    request: CandidateAssistanceRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateAssistanceResponse:
    sessionmaker = get_sessionmaker()
    evidence_coordinator: SessionEvidenceEvaluationCoordinator | None = None
    try:
        provider = build_reasoning_provider(settings)
    except ReasoningProviderConfigurationError:
        # The deterministic policy remains available. Without a configured
        # provider the workflow cannot unlock assistance beyond metacognition.
        provider = None
    if provider is not None:
        evidence_coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessionmaker,
            ai_gateway=AIGateway(
                settings=settings,
                sessionmaker=sessionmaker,
                provider=provider,
            ),
        )
    try:
        result = await CoachAssistanceWorkflow(
            sessionmaker=sessionmaker,
            evidence_coordinator=evidence_coordinator,
        ).request(
            AssistanceRequestCommand(
                interview_session_id=interview_session_id,
                idempotency_key=request.idempotency_key,
            )
        )
    except (InterviewRuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": "assistance_request_rejected", "message": str(exc)},
        ) from exc
    return _response(result)


def _response(result: AssistanceRequestResult) -> CandidateAssistanceResponse:
    return CandidateAssistanceResponse(
        status=result.status,
        reason=result.reason,
        mode=result.mode,
        mode_policy_version=ModePolicy.policy_version,
        request_event_id=result.request_event_id,
        request_event_watermark=result.request_event_watermark,
        interviewer_prompt_id=result.interviewer_prompt_id,
        prompt_kind=result.prompt_kind,
        assistance_type=result.assistance_type,
        hint_level=result.hint_level,
        target_concept_id=result.target_concept_id,
        target_skill_dimension_id=result.target_skill_dimension_id,
        source_code_snapshot_id=result.source_code_snapshot_id,
        invites_guided_retry=result.invites_guided_retry,
        budget=AssistanceBudgetResponse.model_validate(result.budget, from_attributes=True),
    )

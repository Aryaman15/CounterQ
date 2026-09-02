from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider import ReasoningProvider
from app.ai_gateway.provider_factory import (
    ReasoningProviderConfigurationError,
    build_reasoning_provider,
)
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.evidence.snapshot import canonical_evaluation_snapshot

router = APIRouter(prefix="/api/evidence/development", tags=["evidence-development"])


class DevelopmentSessionEvaluationRequest(BaseModel):
    interview_session_id: UUID


class DevelopmentUnitEvaluationResponse(BaseModel):
    unit_key: str
    unit_kind: str
    status: str
    assessment_ids: list[UUID]
    evidence_ids: list[UUID]
    breakpoint_ids: list[UUID]
    error_category: str | None


class DevelopmentSessionEvaluationResponse(BaseModel):
    interview_session_id: UUID
    completed_units: int
    skipped_units: int
    failed_units: int
    units: list[DevelopmentUnitEvaluationResponse]


class DevelopmentCanonicalEvaluationSnapshot(BaseModel):
    interview_session_id: UUID
    assessments: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    breakpoints: list[dict[str, Any]]


def get_evidence_provider_builder() -> Callable[[Settings], ReasoningProvider]:
    return build_reasoning_provider


def _require_development(settings: Settings) -> None:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Stage 5 evaluation inspection is development-only",
            },
        )


@router.post(
    "/session-evaluation",
    response_model=DevelopmentSessionEvaluationResponse,
)
async def evaluate_session(
    request: DevelopmentSessionEvaluationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], ReasoningProvider], Depends(get_evidence_provider_builder)
    ],
) -> DevelopmentSessionEvaluationResponse:
    _require_development(settings)
    try:
        provider = provider_builder(settings)
    except ReasoningProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": "configuration_error", "message": str(exc)},
        ) from exc
    sessionmaker = get_sessionmaker()
    try:
        result = await SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessionmaker,
            ai_gateway=AIGateway(
                settings=settings,
                sessionmaker=sessionmaker,
                provider=provider,
            ),
        ).evaluate(request.interview_session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": "session_not_evaluable", "message": str(exc)},
        ) from exc
    return DevelopmentSessionEvaluationResponse(
        interview_session_id=result.interview_session_id,
        completed_units=result.completed_units,
        skipped_units=result.skipped_units,
        failed_units=result.failed_units,
        units=[
            DevelopmentUnitEvaluationResponse(
                unit_key=unit.unit_key,
                unit_kind=unit.unit_kind,
                status=unit.status,
                assessment_ids=list(unit.assessment_ids),
                evidence_ids=list(unit.evidence_ids),
                breakpoint_ids=list(unit.breakpoint_ids),
                error_category=unit.error_category,
            )
            for unit in result.units
        ],
    )


@router.get(
    "/session-evaluation/{interview_session_id}",
    response_model=DevelopmentCanonicalEvaluationSnapshot,
)
async def evaluation_snapshot(
    interview_session_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevelopmentCanonicalEvaluationSnapshot:
    _require_development(settings)
    try:
        snapshot = await canonical_evaluation_snapshot(session, interview_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DevelopmentCanonicalEvaluationSnapshot.model_validate(snapshot)

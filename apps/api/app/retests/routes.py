"""Current-candidate and development adapters for the Stage 8B retest service."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.principal import CurrentUser
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_sessionmaker
from app.mastery.service import MasteryRecalculationService
from app.retests.development import (
    DEVELOPMENT_RETEST_SUBJECT,
    active_development_recommendation,
    ensure_development_retest_fixture,
)
from app.retests.service import RetestLaunch, RetestService, RetestStartError

router = APIRouter(prefix="/api/retests", tags=["retests"])


class RetestContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DevelopmentRetestFixtureResponse(RetestContractModel):
    user_id: UUID
    recommendation_id: UUID
    mastery_path: str = "/retests/demo"


class RetestLaunchResponse(RetestContractModel):
    recommendation_id: UUID
    retest_attempt_id: UUID
    interview_session_id: UUID
    problem_id: UUID
    problem_version_id: UUID
    problem_title: str
    language: Literal["cpp", "python", "java"]
    target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    mode: Literal["SIMULATION"]
    template: Literal["QUICK_DRILL"]
    configured_duration_seconds: Literal[600]
    resumed: bool
    interview_path: str


@router.post(
    "/recommendations/{recommendation_id}/start",
    response_model=RetestLaunchResponse,
)
async def start_retest(
    recommendation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> RetestLaunchResponse:
    try:
        launch = await RetestService(sessionmaker=get_sessionmaker()).start(
            principal_user_id=current_user.id,
            recommendation_id=recommendation_id,
        )
    except RetestStartError as error:
        response_status = (
            status.HTTP_404_NOT_FOUND
            if error.category == "RECOMMENDATION_NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=response_status,
            detail={"category": error.category, "message": error.safe_message},
        ) from error
    return _launch_response(
        launch,
        interview_path=f"/interview/{launch.interview_session_id}",
    )


@router.post(
    "/development/fixture",
    response_model=DevelopmentRetestFixtureResponse,
)
async def development_retest_fixture(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevelopmentRetestFixtureResponse:
    """Materialize canonical durable inputs, then run normal Mastery projection."""

    _require_development(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        user = await ensure_development_retest_fixture(session)
        user_id = user.id
    await MasteryRecalculationService(sessionmaker=sessionmaker).recalculate(user_id=user_id)
    async with sessionmaker() as session:
        recommendation = await active_development_recommendation(session, user_id)
    if recommendation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "NO_ACTIONABLE_RETEST",
                "message": "No suitable retest is available yet.",
            },
        )
    return DevelopmentRetestFixtureResponse(
        user_id=user_id,
        recommendation_id=recommendation.id,
    )


@router.post(
    "/development/recommendations/{recommendation_id}/start",
    response_model=RetestLaunchResponse,
)
async def development_start_retest(
    recommendation_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RetestLaunchResponse:
    """Use the fixed development principal at the future authenticated boundary."""

    _require_development(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        principal_user_id = await session.scalar(
            select(User.id).where(
                User.external_auth_provider == "dev",
                User.external_auth_subject == DEVELOPMENT_RETEST_SUBJECT,
            )
        )
    if principal_user_id is None:
        raise HTTPException(status_code=404, detail="This retest is unavailable.")
    try:
        launch = await RetestService(sessionmaker=sessionmaker).start(
            principal_user_id=principal_user_id,
            recommendation_id=recommendation_id,
        )
    except RetestStartError as error:
        response_status = (
            status.HTTP_404_NOT_FOUND
            if error.category == "RECOMMENDATION_NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=response_status,
            detail={"category": error.category, "message": error.safe_message},
        ) from error
    return _launch_response(launch, interview_path="/interview/demo")


def _launch_response(launch: RetestLaunch, *, interview_path: str) -> RetestLaunchResponse:
    return RetestLaunchResponse(
        recommendation_id=launch.recommendation_id,
        retest_attempt_id=launch.retest_attempt_id,
        interview_session_id=launch.interview_session_id,
        problem_id=launch.problem_id,
        problem_version_id=launch.problem_version_id,
        problem_title=launch.problem_title,
        language=launch.language,  # type: ignore[arg-type]
        target_level=launch.target_level,  # type: ignore[arg-type]
        mode="SIMULATION",
        template="QUICK_DRILL",
        configured_duration_seconds=600,
        resumed=launch.resumed,
        interview_path=interview_path,
    )


def _require_development(settings: Settings) -> None:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "The durable retest harness is development-only.",
            },
        )

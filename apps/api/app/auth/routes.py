from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import CandidateProfile
from app.auth.principal import CurrentUser
from app.auth.repository import CandidateProfileRepository
from app.db.session import get_session

router = APIRouter(prefix="/api/me", tags=["current-user"])


class CurrentUserProfileResponse(BaseModel):
    display_name: str | None
    preferred_language: Literal["cpp", "java", "python"]
    default_interview_mode: Literal["COACH", "SIMULATION"]
    interview_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    target_role: str | None
    timezone: str | None
    profile_version: int
    created_at: datetime
    updated_at: datetime


class CurrentUserResponse(BaseModel):
    user_id: UUID
    account_status: str
    onboarding_required: bool
    profile: CurrentUserProfileResponse | None


class CandidateProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    preferred_language: Literal["cpp", "java", "python"]
    default_interview_mode: Literal["COACH", "SIMULATION"]
    interview_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    target_role: str | None = Field(default=None, max_length=160)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("display_name", "target_role", "timezone", mode="after")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


@router.get("", response_model=CurrentUserResponse)
async def current_user(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUserResponse:
    profile = await CandidateProfileRepository(session).get(current.id)
    return _response(current, profile)


@router.put("/profile", response_model=CurrentUserResponse)
async def update_current_user_profile(
    request: CandidateProfileUpdate,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUserResponse:
    async with session.begin():
        profile = await CandidateProfileRepository(session).upsert(
            user_id=current.id,
            display_name=request.display_name,
            preferred_language=request.preferred_language,
            default_interview_mode=request.default_interview_mode,
            interview_level=request.interview_level,
            target_role=request.target_role,
            timezone=request.timezone,
        )
    return _response(current, profile)


def _response(current: CurrentUser, profile: CandidateProfile | None) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=current.id,
        account_status=current.status,
        onboarding_required=profile is None,
        profile=(
            CurrentUserProfileResponse.model_validate(profile, from_attributes=True)
            if profile is not None
            else None
        ),
    )

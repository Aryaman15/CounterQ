"""Candidate-safe Mastery read models and development-only Stage 8A controls."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.principal import CurrentUser
from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.interviews.models import InterviewSession
from app.mastery.development_fixtures import DEMO_NOW, load_development_mastery_fixtures
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    MasteryTransition,
    RetestRecommendation,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.mastery.policy import MASTERY_POLICY_VERSION
from app.mastery.read import read_candidate_mastery_overview
from app.mastery.schema import (
    CandidateMasteryOverviewResponse,
    DevelopmentMasteryFixtureResponse,
    DevelopmentMasteryInspection,
)
from app.mastery.service import initial_mastery_recalculation_key
from app.mastery.view import build_candidate_mastery_overview
from app.outbox.models import OutboxEvent
from app.outbox.repository import OutboxRepository

router = APIRouter(prefix="/api/mastery", tags=["mastery"])


class DevelopmentMasteryRecalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class DevelopmentMasteryRecalculationResponse(BaseModel):
    outbox_event_id: UUID
    created: bool
    status: str


@router.get("/me", response_model=CandidateMasteryOverviewResponse)
async def current_user_mastery(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateMasteryOverviewResponse:
    return await read_candidate_mastery_overview(session, user_id=current_user.id)


@router.get(
    "/development/fixtures",
    response_model=list[DevelopmentMasteryFixtureResponse],
)
async def development_mastery_fixtures(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[DevelopmentMasteryFixtureResponse]:
    _require_development(settings)
    return [
        DevelopmentMasteryFixtureResponse(
            fixture_id=fixture.fixture_id,
            label=fixture.label,
            description=fixture.description,
            overview=build_candidate_mastery_overview(fixture.bundle, now=DEMO_NOW),
        )
        for fixture in load_development_mastery_fixtures()
    ]


@router.get(
    "/development/users/{user_id}",
    response_model=CandidateMasteryOverviewResponse,
)
async def development_user_mastery(
    user_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateMasteryOverviewResponse:
    _require_development(settings)
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Mastery user was not found")
    return await read_candidate_mastery_overview(session, user_id=user_id)


@router.post(
    "/development/users/{user_id}/recalculate",
    response_model=DevelopmentMasteryRecalculationResponse,
)
async def development_recalculate_mastery(
    user_id: UUID,
    request: DevelopmentMasteryRecalculationRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DevelopmentMasteryRecalculationResponse:
    _require_development(settings)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        interview = await session.scalar(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(InterviewSession.completed_at.desc().nullslast(), InterviewSession.id.desc())
            .limit(1)
            .with_for_update()
        )
        if interview is None:
            raise HTTPException(
                status_code=409,
                detail="A canonical development session is required to anchor outbox work",
            )
        key = (
            f"{initial_mastery_recalculation_key(user_id, interview.id)}:"
            f"manual:{request.idempotency_key}"
        )
        event, created = await OutboxRepository(session).enqueue(
            aggregate_type="User",
            aggregate_id=user_id,
            interview_session_id=interview.id,
            event_type="RECALCULATE_MASTERY",
            payload={
                "user_id": str(user_id),
                "source_interview_session_id": str(interview.id),
                "mastery_policy_version": MASTERY_POLICY_VERSION,
            },
            deduplication_key=key,
            available_at=datetime.now(UTC),
            source_watermark=interview.last_server_sequence,
        )
        return DevelopmentMasteryRecalculationResponse(
            outbox_event_id=event.id, created=created, status=event.status
        )


@router.get(
    "/development/users/{user_id}/inspection",
    response_model=DevelopmentMasteryInspection,
)
async def development_mastery_inspection(
    user_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevelopmentMasteryInspection:
    _require_development(settings)
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Mastery user was not found")
    concept_rows = list(
        await session.scalars(select(ConceptMastery).where(ConceptMastery.user_id == user_id))
    )
    skill_rows = list(
        await session.scalars(select(SkillMastery).where(SkillMastery.user_id == user_id))
    )
    recommendations = list(
        await session.scalars(
            select(RetestRecommendation).where(RetestRecommendation.user_id == user_id)
        )
    )
    jobs = list(
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == "User",
                OutboxEvent.aggregate_id == user_id,
                OutboxEvent.event_type == "RECALCULATE_MASTERY",
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
    )
    latest = jobs[-1] if jobs else None
    states = Counter(row.state for row in concept_rows)
    states.update(row.state for row in skill_rows)
    return DevelopmentMasteryInspection(
        user_id=user_id,
        mastery_policy_version=_projection_policy_version(concept_rows, skill_rows),
        recalculation_state=latest.status if latest else "NOT_STARTED",
        concept_projection_count=len(concept_rows),
        skill_projection_count=len(skill_rows),
        concept_association_count=int(
            await session.scalar(
                select(func.count())
                .select_from(ConceptMasteryEvidence)
                .where(ConceptMasteryEvidence.user_id == user_id)
            )
            or 0
        ),
        skill_association_count=int(
            await session.scalar(
                select(func.count())
                .select_from(SkillMasteryEvidence)
                .where(SkillMasteryEvidence.user_id == user_id)
            )
            or 0
        ),
        transition_count=int(
            await session.scalar(
                select(func.count())
                .select_from(MasteryTransition)
                .where(MasteryTransition.user_id == user_id)
            )
            or 0
        ),
        recommendation_count=len(recommendations),
        recommendation_statuses=dict(
            sorted(Counter(item.status for item in recommendations).items())
        ),
        state_distribution=dict(sorted(states.items())),
        latest_failure_category=latest.last_error if latest and latest.status == "FAILED" else None,
    )


def _projection_policy_version(
    concept_rows: list[ConceptMastery],
    skill_rows: list[SkillMastery],
) -> str:
    versions = {
        *(item.mastery_policy_version for item in concept_rows),
        *(item.mastery_policy_version for item in skill_rows),
    }
    if not versions:
        return MASTERY_POLICY_VERSION
    return next(iter(versions)) if len(versions) == 1 else "mixed_projection_versions"


def _require_development(settings: Settings) -> None:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Mastery demo, recalculation, and inspection are development-only",
            },
        )

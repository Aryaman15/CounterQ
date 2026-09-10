"""Shared persisted Mastery reader for candidate and development HTTP adapters."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewSession
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    RetestAttempt,
    RetestRecommendation,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.mastery.policy import ContributionClassification, MasteryState
from app.mastery.schema import CandidateMasteryOverviewResponse
from app.mastery.source import MasterySourceBuilder
from app.mastery.view import PersistedMasteryProjection, build_persisted_candidate_mastery_overview
from app.outbox.models import OutboxEvent


async def read_candidate_mastery_overview(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> CandidateMasteryOverviewResponse:
    bundle = await MasterySourceBuilder(session).build(user_id, admitted_only=True)
    concept_rows = list(
        await session.scalars(select(ConceptMastery).where(ConceptMastery.user_id == user_id))
    )
    skill_rows = list(
        await session.scalars(select(SkillMastery).where(SkillMastery.user_id == user_id))
    )
    concept_links = list(
        await session.scalars(
            select(ConceptMasteryEvidence).where(ConceptMasteryEvidence.user_id == user_id)
        )
    )
    skill_links = list(
        await session.scalars(
            select(SkillMasteryEvidence).where(SkillMasteryEvidence.user_id == user_id)
        )
    )
    recommendation_ids: dict[tuple[str, UUID], UUID] = {}
    recommendation_statuses: dict[UUID, Literal["PENDING", "SCHEDULED"]] = {}
    resumable_attempt = (
        select(RetestAttempt.id)
        .join(InterviewSession, InterviewSession.id == RetestAttempt.interview_session_id)
        .where(
            RetestAttempt.retest_recommendation_id == RetestRecommendation.id,
            RetestAttempt.outcome.is_(None),
            RetestAttempt.completed_at.is_(None),
            InterviewSession.status.in_(("READY", "ACTIVE", "RECONNECTING")),
        )
        .exists()
    )
    for row in await session.scalars(
        select(RetestRecommendation)
        .where(
            RetestRecommendation.user_id == user_id,
            or_(
                RetestRecommendation.status == "PENDING",
                and_(RetestRecommendation.status == "SCHEDULED", resumable_attempt),
            ),
        )
        .order_by(
            RetestRecommendation.priority.desc(),
            RetestRecommendation.recommended_after,
            RetestRecommendation.created_at,
            RetestRecommendation.id,
        )
    ):
        target_id = row.concept_id or row.skill_dimension_id
        if target_id is None:
            continue
        target_type = "CONCEPT" if row.concept_id is not None else "SKILL"
        recommendation_statuses[row.id] = cast(
            Literal["PENDING", "SCHEDULED"], row.status
        )
        key = (target_type, target_id)
        selected_id = recommendation_ids.get(key)
        if selected_id is None or (
            row.status == "SCHEDULED" and recommendation_statuses[selected_id] == "PENDING"
        ):
            recommendation_ids[key] = row.id
    latest_job = await session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_type == "User",
            OutboxEvent.aggregate_id == user_id,
            OutboxEvent.event_type == "RECALCULATE_MASTERY",
        )
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
        .limit(1)
    )
    response_status = None
    if latest_job and latest_job.status in {"PENDING", "PUBLISHED", "PROCESSING", "RETRY"}:
        response_status = "UPDATING"
    elif latest_job and latest_job.status == "FAILED":
        response_status = "FAILED"
    return build_persisted_candidate_mastery_overview(
        bundle,
        _persisted_projections(concept_rows, skill_rows, concept_links, skill_links),
        recommendation_ids=recommendation_ids,
        recommendation_statuses=recommendation_statuses,
        status=response_status,
    )


def _persisted_projections(
    concept_rows: list[ConceptMastery],
    skill_rows: list[SkillMastery],
    concept_links: list[ConceptMasteryEvidence],
    skill_links: list[SkillMasteryEvidence],
) -> tuple[PersistedMasteryProjection, ...]:
    concept_contributions: dict[
        UUID, list[tuple[UUID, ContributionClassification, str]]
    ] = {}
    for link in concept_links:
        concept_contributions.setdefault(link.concept_id, []).append(
            (
                link.evidence_id,
                cast(ContributionClassification, link.contribution_classification),
                link.context_key,
            )
        )
    skill_contributions: dict[
        UUID, list[tuple[UUID, ContributionClassification, str]]
    ] = {}
    for skill_link in skill_links:
        skill_contributions.setdefault(skill_link.skill_dimension_id, []).append(
            (
                skill_link.evidence_id,
                cast(ContributionClassification, skill_link.contribution_classification),
                skill_link.context_key,
            )
        )
    projections = [
        PersistedMasteryProjection(
            family="CONCEPT",
            target_id=row.concept_id,
            state=cast(MasteryState, row.state),
            mastery_policy_version=row.mastery_policy_version,
            projection_version=row.projection_version,
            last_evaluated_at=row.last_evaluated_at,
            last_evidence_at=row.last_evidence_at,
            supporting_evidence_count=row.supporting_evidence_count,
            context_diversity=row.context_diversity,
            updated_at=row.updated_at,
            contributions=tuple(
                sorted(concept_contributions.get(row.concept_id, []), key=lambda item: str(item[0]))
            ),
        )
        for row in concept_rows
    ]
    projections.extend(
        PersistedMasteryProjection(
            family="SKILL",
            target_id=row.skill_dimension_id,
            state=cast(MasteryState, row.state),
            mastery_policy_version=row.mastery_policy_version,
            projection_version=row.projection_version,
            last_evaluated_at=row.last_evaluated_at,
            last_evidence_at=row.last_evidence_at,
            supporting_evidence_count=row.supporting_evidence_count,
            context_diversity=row.context_diversity,
            updated_at=row.updated_at,
            contributions=tuple(
                sorted(
                    skill_contributions.get(row.skill_dimension_id, []),
                    key=lambda item: str(item[0]),
                )
            ),
        )
        for row in skill_rows
    )
    return tuple(sorted(projections, key=lambda item: (item.family, str(item.target_id))))

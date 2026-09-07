"""Idempotent retest Evidence linkage and workflow finalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.models import (
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
)
from app.interviews.models import InterviewSession
from app.mastery.models import RetestAttempt, RetestAttemptEvidence, RetestRecommendation
from app.mastery.policy import MasteryProjectionDecision, RetestReason
from app.mastery.source import MasterySourceBuilder, MasteryTargetSource, TargetFamily
from app.retests.policy import (
    RetestOutcomeDecision,
    RetestOutcomePolicyV1,
    recommendation_reason,
)


@dataclass(slots=True)
class PreparedRetestFinalization:
    attempt: RetestAttempt
    recommendation: RetestRecommendation
    original_reason: RetestReason
    candidate_outcome: RetestOutcomeDecision
    target_family: TargetFamily
    target_id: UUID
    breakpoint_resolved: bool


class RetestAttemptFinalizer:
    """Runs inside the existing user-wide Mastery transaction and lock."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: RetestOutcomePolicyV1 | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or RetestOutcomePolicyV1()

    async def prepare(self, *, user_id: UUID, now: datetime) -> list[PreparedRetestFinalization]:
        rows = list(
            (
                await self._session.execute(
                    select(RetestAttempt, RetestRecommendation, InterviewSession)
                    .join(
                        RetestRecommendation,
                        RetestRecommendation.id == RetestAttempt.retest_recommendation_id,
                    )
                    .join(
                        InterviewSession,
                        InterviewSession.id == RetestAttempt.interview_session_id,
                    )
                    .where(
                        RetestRecommendation.user_id == user_id,
                        RetestAttempt.outcome.is_(None),
                        InterviewSession.status.in_(("COMPLETED", "ABANDONED")),
                    )
                    .order_by(RetestAttempt.started_at, RetestAttempt.id)
                    .with_for_update()
                )
            ).all()
        )
        for attempt, recommendation, _interview in rows:
            await self._link_target_evidence(attempt, recommendation)
        if rows:
            await self._session.flush()

        bundle = await MasterySourceBuilder(self._session).build(user_id)
        targets = {(item.family, item.target_id): item for item in bundle.targets}
        prepared: list[PreparedRetestFinalization] = []
        for attempt, recommendation, interview in rows:
            family, target_id = _target_identity(recommendation)
            target = targets.get((family, target_id))
            attempt_facts = tuple(
                item
                for item in (target.facts.evidence if target is not None else ())
                if item.session_id == attempt.interview_session_id and item.is_retest
            )
            reason = recommendation_reason(recommendation.recommendation_key)
            candidate = self._policy.evaluate(
                attempt_facts,
                original_reason=reason,
                session_abandoned=interview.status == "ABANDONED",
            )
            breakpoint_resolved = await self._apply_breakpoint_evidence(
                recommendation=recommendation,
                outcome=candidate,
                now=now,
            )
            prepared.append(
                PreparedRetestFinalization(
                    attempt=attempt,
                    recommendation=recommendation,
                    original_reason=reason,
                    candidate_outcome=candidate,
                    target_family=family,
                    target_id=target_id,
                    breakpoint_resolved=breakpoint_resolved,
                )
            )
        return prepared

    async def finalize_target(
        self,
        prepared: list[PreparedRetestFinalization],
        *,
        target: MasteryTargetSource,
        decision: MasteryProjectionDecision,
        now: datetime,
    ) -> None:
        for item in prepared:
            if (item.target_family, item.target_id) != (target.family, target.target_id):
                continue
            outcome = item.candidate_outcome.outcome
            if outcome == "SATISFIED":
                same_reason_due = decision.retest_reason == item.original_reason
                breakpoint_open = bool(
                    item.recommendation.breakpoint_id is not None and not item.breakpoint_resolved
                )
                if same_reason_due or breakpoint_open:
                    outcome = "INCONCLUSIVE"
            item.attempt.outcome = outcome
            item.attempt.completed_at = now
            item.recommendation.status = "SATISFIED" if outcome == "SATISFIED" else "ATTEMPTED"
            item.recommendation.updated_at = now

    async def finalize_unmatched(
        self, prepared: list[PreparedRetestFinalization], *, now: datetime
    ) -> None:
        for item in prepared:
            if item.attempt.outcome is not None:
                continue
            outcome = item.candidate_outcome.outcome
            if outcome == "SATISFIED":
                outcome = "INCONCLUSIVE"
            item.attempt.outcome = outcome
            item.attempt.completed_at = now
            item.recommendation.status = "ATTEMPTED"
            item.recommendation.updated_at = now

    async def _link_target_evidence(
        self, attempt: RetestAttempt, recommendation: RetestRecommendation
    ) -> None:
        query = select(Evidence.id).where(
            Evidence.interview_session_id == attempt.interview_session_id,
            Evidence.validation_status == "VALID",
            Evidence.invalidated_at.is_(None),
        )
        if recommendation.concept_id is not None:
            query = query.join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id).where(
                EvidenceConcept.concept_id == recommendation.concept_id
            )
        elif recommendation.skill_dimension_id is not None:
            query = query.join(EvidenceSkill, EvidenceSkill.evidence_id == Evidence.id).where(
                EvidenceSkill.skill_dimension_id == recommendation.skill_dimension_id
            )
        else:
            return
        for evidence_id in await self._session.scalars(
            query.order_by(Evidence.created_at, Evidence.id)
        ):
            await self._session.execute(
                insert(RetestAttemptEvidence)
                .values(retest_attempt_id=attempt.id, evidence_id=evidence_id)
                .on_conflict_do_nothing(
                    index_elements=(
                        RetestAttemptEvidence.retest_attempt_id,
                        RetestAttemptEvidence.evidence_id,
                    )
                )
            )

    async def _apply_breakpoint_evidence(
        self,
        *,
        recommendation: RetestRecommendation,
        outcome: RetestOutcomeDecision,
        now: datetime,
    ) -> bool:
        if recommendation.breakpoint_id is None:
            return False
        breakpoint = await self._session.scalar(
            select(Breakpoint)
            .where(Breakpoint.id == recommendation.breakpoint_id)
            .with_for_update()
        )
        if (
            breakpoint is None
            or breakpoint.user_id != recommendation.user_id
            or breakpoint.concept_id != recommendation.concept_id
        ):
            return False
        if breakpoint.status in {"RESOLVED", "DISMISSED"}:
            return breakpoint.status == "RESOLVED"

        if outcome.outcome == "PERSISTED_GAP":
            evidence_ids = await self._exact_breakpoint_evidence_ids(
                breakpoint, outcome.qualifying_negative_ids
            )
            for evidence_id in evidence_ids:
                await _link_breakpoint(self._session, breakpoint.id, evidence_id, "REINFORCED")
            return False
        if outcome.outcome != "SATISFIED":
            return False
        evidence_ids = await self._exact_breakpoint_evidence_ids(
            breakpoint, outcome.qualifying_positive_ids
        )
        if not evidence_ids:
            return False
        for evidence_id in evidence_ids:
            await _link_breakpoint(self._session, breakpoint.id, evidence_id, "RESOLUTION_SUPPORT")
        breakpoint.status = "RESOLVED"
        breakpoint.resolved_at = now
        breakpoint.resolution_reason = "INDEPENDENT_RETEST_VERIFIED"
        return True

    async def _exact_breakpoint_evidence_ids(
        self, breakpoint: Breakpoint, evidence_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        if not evidence_ids:
            return ()
        values = await self._session.scalars(
            select(Evidence.id)
            .join(EvidenceConcept, EvidenceConcept.evidence_id == Evidence.id)
            .join(EvidenceSkill, EvidenceSkill.evidence_id == Evidence.id)
            .where(
                Evidence.id.in_(evidence_ids),
                Evidence.validation_status == "VALID",
                Evidence.invalidated_at.is_(None),
                EvidenceConcept.concept_id == breakpoint.concept_id,
                EvidenceSkill.skill_dimension_id == breakpoint.skill_dimension_id,
            )
            .order_by(Evidence.created_at, Evidence.id)
        )
        return tuple(values)


async def _link_breakpoint(
    session: AsyncSession,
    breakpoint_id: UUID,
    evidence_id: UUID,
    relationship: str,
) -> None:
    existing = await session.scalar(
        select(BreakpointEvidence).where(
            BreakpointEvidence.breakpoint_id == breakpoint_id,
            BreakpointEvidence.evidence_id == evidence_id,
        )
    )
    if existing is None:
        session.add(
            BreakpointEvidence(
                breakpoint_id=breakpoint_id,
                evidence_id=evidence_id,
                relationship=relationship,
            )
        )


def _target_identity(recommendation: RetestRecommendation) -> tuple[TargetFamily, UUID]:
    if recommendation.concept_id is not None:
        return "CONCEPT", recommendation.concept_id
    if recommendation.skill_dimension_id is not None:
        return "SKILL", recommendation.skill_dimension_id
    raise ValueError("RetestRecommendation has no canonical target")

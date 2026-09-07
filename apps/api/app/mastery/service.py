"""Serialized, idempotent user-wide Mastery recomputation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeGuard
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User
from app.evidence.models import Evidence
from app.mastery.models import (
    ConceptMastery,
    ConceptMasteryEvidence,
    MasteryTransition,
    MasteryTransitionEvidence,
    RetestAttempt,
    RetestRecommendation,
    SkillMastery,
    SkillMasteryEvidence,
)
from app.mastery.policy import (
    MASTERY_POLICY_VERSION,
    MasteryPolicyV1,
    MasteryProjectionDecision,
    RetestReason,
)
from app.mastery.source import MasterySourceBuilder, MasteryTargetSource
from app.outbox.claims import OutboxWorkClaim
from app.outbox.models import OutboxEvent
from app.retests.finalization import RetestAttemptFinalizer


class MasteryRecalculationError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class MasteryWorkOwnershipLost(MasteryRecalculationError):
    def __init__(self) -> None:
        super().__init__("OUTBOX_OWNERSHIP_LOST", "Mastery work claim is no longer current")


@dataclass(frozen=True, slots=True)
class MasteryRecalculationResult:
    user_id: UUID
    target_level: str
    concept_projection_count: int
    skill_projection_count: int
    association_count: int
    transition_count: int
    pending_recommendation_count: int


class MasteryRecalculationService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        policy: MasteryPolicyV1 | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._policy = policy or MasteryPolicyV1()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def recalculate(
        self,
        *,
        user_id: UUID,
        work_claim: OutboxWorkClaim | None = None,
    ) -> MasteryRecalculationResult:
        return await self._recalculate(user_id=user_id, work_claim=work_claim)

    async def recalculate_for_development(
        self,
        *,
        user_id: UUID,
        target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"],
        work_claim: OutboxWorkClaim | None = None,
    ) -> MasteryRecalculationResult:
        """Explicit test/development seam; production work never accepts this override."""

        return await self._recalculate(
            user_id=user_id,
            work_claim=work_claim,
            development_target_level=target_level,
        )

    async def _recalculate(
        self,
        *,
        user_id: UUID,
        work_claim: OutboxWorkClaim | None,
        development_target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
        | None = None,
    ) -> MasteryRecalculationResult:
        async with self._sessionmaker() as session, session.begin():
            if work_claim is not None:
                await _assert_work_claim(session, work_claim)
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None:
                raise MasteryRecalculationError("USER_NOT_FOUND", "Mastery user was not found")
            requested_now = self._clock()
            if requested_now.tzinfo is None:
                raise MasteryRecalculationError(
                    "INVALID_CLOCK", "Mastery clock must be timezone-aware"
                )
            now = await _monotonic_projection_time(session, user_id, requested_now)
            retest_finalizer = RetestAttemptFinalizer(session)
            prepared_retests = await retest_finalizer.prepare(user_id=user_id, now=now)
            bundle = await MasterySourceBuilder(session).build(
                user_id,
                target_level=development_target_level,
            )
            transition_count = 0
            association_count = 0
            for target in bundle.targets:
                decision = self._policy.evaluate(target.facts, now=now)
                changed, transition_created = await _persist_projection(
                    session,
                    user_id=user_id,
                    target=target,
                    decision=decision,
                    policy_version=self._policy.version,
                    now=now,
                )
                association_count += len(decision.contributions)
                transition_count += int(transition_created)
                await retest_finalizer.finalize_target(
                    prepared_retests,
                    target=target,
                    decision=decision,
                    now=now,
                )
                await _sync_recommendation(
                    session,
                    user_id=user_id,
                    target=target,
                    decision=decision,
                    policy_version=self._policy.version,
                    now=now,
                    projection_changed=changed,
                )
            await retest_finalizer.finalize_unmatched(prepared_retests, now=now)
            concept_count = sum(item.family == "CONCEPT" for item in bundle.targets)
            skill_count = sum(item.family == "SKILL" for item in bundle.targets)
            pending_count = len(
                list(
                    await session.scalars(
                        select(RetestRecommendation.id).where(
                            RetestRecommendation.user_id == user_id,
                            RetestRecommendation.status == "PENDING",
                        )
                    )
                )
            )
            return MasteryRecalculationResult(
                user_id=user_id,
                target_level=bundle.target_level,
                concept_projection_count=concept_count,
                skill_projection_count=skill_count,
                association_count=association_count,
                transition_count=transition_count,
                pending_recommendation_count=pending_count,
            )


async def _persist_projection(
    session: AsyncSession,
    *,
    user_id: UUID,
    target: MasteryTargetSource,
    decision: MasteryProjectionDecision,
    policy_version: str,
    now: datetime,
) -> tuple[bool, bool]:
    row: ConceptMastery | SkillMastery | None
    links: list[ConceptMasteryEvidence] | list[SkillMasteryEvidence]
    if target.family == "CONCEPT":
        row = await session.scalar(
            select(ConceptMastery)
            .where(
                ConceptMastery.user_id == user_id,
                ConceptMastery.concept_id == target.target_id,
            )
            .with_for_update()
        )
        links = list(
            await session.scalars(
                select(ConceptMasteryEvidence).where(
                    ConceptMasteryEvidence.user_id == user_id,
                    ConceptMasteryEvidence.concept_id == target.target_id,
                )
            )
        )
    else:
        row = await session.scalar(
            select(SkillMastery)
            .where(
                SkillMastery.user_id == user_id,
                SkillMastery.skill_dimension_id == target.target_id,
            )
            .with_for_update()
        )
        links = list(
            await session.scalars(
                select(SkillMasteryEvidence).where(
                    SkillMasteryEvidence.user_id == user_id,
                    SkillMasteryEvidence.skill_dimension_id == target.target_id,
                )
            )
        )
    previous_state = row.state if row is not None else "UNTESTED"
    previous_projection_version = row.projection_version if row is not None else 0
    existing_links = {
        item.evidence_id: (
            item.contribution_classification,
            item.context_key,
            item.admitted_policy_version,
        )
        for item in links
    }
    desired_links = {
        item.evidence_id: (item.classification, item.context_key, policy_version)
        for item in decision.contributions
    }
    support_count = len(decision.supporting_evidence_ids)
    semantic_changed = bool(
        row is None
        or row.state != decision.state
        or row.mastery_policy_version != policy_version
        or row.last_evidence_at != decision.last_evidence_at
        or row.supporting_evidence_count != support_count
        or row.context_diversity != decision.distinct_context_count
        or existing_links != desired_links
    )
    if row is None:
        if target.family == "CONCEPT":
            row = ConceptMastery(
                user_id=user_id,
                concept_id=target.target_id,
                state=decision.state,
                last_evaluated_at=now,
                last_evidence_at=decision.last_evidence_at,
                mastery_policy_version=policy_version,
                projection_version=1,
                supporting_evidence_count=support_count,
                context_diversity=decision.distinct_context_count,
                updated_at=now,
            )
        else:
            row = SkillMastery(
                user_id=user_id,
                skill_dimension_id=target.target_id,
                state=decision.state,
                last_evaluated_at=now,
                last_evidence_at=decision.last_evidence_at,
                mastery_policy_version=policy_version,
                projection_version=1,
                supporting_evidence_count=support_count,
                context_diversity=decision.distinct_context_count,
                updated_at=now,
            )
        session.add(row)
        await session.flush()
    else:
        row.state = decision.state
        row.last_evaluated_at = now
        row.last_evidence_at = decision.last_evidence_at
        row.mastery_policy_version = policy_version
        row.supporting_evidence_count = support_count
        row.context_diversity = decision.distinct_context_count
        row.updated_at = now
        if semantic_changed:
            row.projection_version += 1

    await _sync_links(
        session,
        user_id=user_id,
        target=target,
        existing=links,
        desired=decision,
        policy_version=policy_version,
        now=now,
    )
    transition_created = False
    if previous_state != decision.state:
        transition_key = _transition_key(
            user_id,
            target,
            previous_projection_version,
            previous_state,
            decision.state,
            frozenset(existing_links),
            frozenset(desired_links),
            policy_version,
        )
        existing_transition = await session.scalar(
            select(MasteryTransition).where(MasteryTransition.transition_key == transition_key)
        )
        if existing_transition is None:
            transition = MasteryTransition(
                user_id=user_id,
                target_type=target.family,
                concept_id=target.target_id if target.family == "CONCEPT" else None,
                skill_dimension_id=target.target_id if target.family == "SKILL" else None,
                from_state=previous_state,
                to_state=decision.state,
                mastery_policy_version=policy_version,
                transition_key=transition_key,
                created_at=now,
            )
            session.add(transition)
            await session.flush()
            audit_evidence_ids = frozenset(existing_links) | frozenset(desired_links)
            existing_evidence_ids = frozenset(
                await session.scalars(
                    select(Evidence.id).where(Evidence.id.in_(audit_evidence_ids))
                )
            )
            session.add_all(
                MasteryTransitionEvidence(
                    mastery_transition_id=transition.id, evidence_id=evidence_id
                )
                for evidence_id in sorted(existing_evidence_ids, key=str)
            )
            transition_created = True
    return semantic_changed, transition_created


async def _sync_links(
    session: AsyncSession,
    *,
    user_id: UUID,
    target: MasteryTargetSource,
    existing: list[ConceptMasteryEvidence] | list[SkillMasteryEvidence],
    desired: MasteryProjectionDecision,
    policy_version: str,
    now: datetime,
) -> None:
    desired_by_id = {item.evidence_id: item for item in desired.contributions}
    existing_by_id = {item.evidence_id: item for item in existing}
    for evidence_id, link in existing_by_id.items():
        wanted = desired_by_id.get(evidence_id)
        if wanted is None:
            await session.delete(link)
            continue
        link.contribution_classification = wanted.classification
        link.context_key = wanted.context_key
        link.admitted_policy_version = policy_version
    for evidence_id, contribution in desired_by_id.items():
        if evidence_id in existing_by_id:
            continue
        if target.family == "CONCEPT":
            link = ConceptMasteryEvidence(
                user_id=user_id,
                concept_id=target.target_id,
                evidence_id=evidence_id,
                contribution_classification=contribution.classification,
                context_key=contribution.context_key,
                admitted_policy_version=policy_version,
                admitted_at=now,
            )
        else:
            link = SkillMasteryEvidence(
                user_id=user_id,
                skill_dimension_id=target.target_id,
                evidence_id=evidence_id,
                contribution_classification=contribution.classification,
                context_key=contribution.context_key,
                admitted_policy_version=policy_version,
                admitted_at=now,
            )
        session.add(link)


async def _sync_recommendation(
    session: AsyncSession,
    *,
    user_id: UUID,
    target: MasteryTargetSource,
    decision: MasteryProjectionDecision,
    policy_version: str,
    now: datetime,
    projection_changed: bool,
) -> None:
    active = list(
        await session.scalars(
            select(RetestRecommendation)
            .where(
                RetestRecommendation.user_id == user_id,
                RetestRecommendation.concept_id
                == (target.target_id if target.family == "CONCEPT" else None),
                RetestRecommendation.skill_dimension_id
                == (target.target_id if target.family == "SKILL" else None),
                RetestRecommendation.status.in_(("PENDING", "SCHEDULED")),
            )
            .with_for_update()
        )
    )
    if target.family == "SKILL":
        for item in active:
            item.status = "SUPERSEDED"
            item.updated_at = now
        return
    if not decision.retest_due or decision.retest_reason is None:
        for item in active:
            item.status = "SUPERSEDED"
            item.updated_at = now
        return
    evidence_identity = _hash(*(str(item.evidence_id) for item in decision.contributions))
    breakpoint_id = (
        decision.unresolved_breakpoint_ids[0]
        if decision.retest_reason == RetestReason.UNRESOLVED_BREAKPOINT
        and decision.unresolved_breakpoint_ids
        else None
    )
    breakpoint_identity = str(breakpoint_id) if breakpoint_id is not None else "none"
    recommendation_key = (
        f"mastery-retest:{user_id}:{target.family.lower()}:{target.target_id}:"
        f"{decision.retest_reason.value}:{policy_version}:breakpoint:{breakpoint_identity}:"
        f"{evidence_identity}"
    )
    latest_completed_attempt_id = await session.scalar(
        select(RetestAttempt.id)
        .join(
            RetestRecommendation,
            RetestRecommendation.id == RetestAttempt.retest_recommendation_id,
        )
        .where(
            RetestRecommendation.user_id == user_id,
            RetestRecommendation.concept_id == target.target_id,
            RetestRecommendation.skill_dimension_id.is_(None),
            RetestAttempt.completed_at.is_not(None),
        )
        .order_by(RetestAttempt.completed_at.desc(), RetestAttempt.id.desc())
        .limit(1)
    )
    if latest_completed_attempt_id is not None:
        recommendation_key = (
            f"{recommendation_key}:after-attempt:{latest_completed_attempt_id}"
        )
    matching = next(
        (item for item in active if item.recommendation_key == recommendation_key), None
    )
    for item in active:
        if item is not matching:
            item.status = "SUPERSEDED"
            item.updated_at = now
    if matching is not None:
        if projection_changed:
            matching.updated_at = now
        return
    priority = {
        RetestReason.UNRESOLVED_BREAKPOINT: 100,
        RetestReason.INDEPENDENCE_NOT_VERIFIED: 85,
        RetestReason.CONTRADICTORY_EVIDENCE: 80,
        RetestReason.STALE_VERIFICATION: 60,
    }[decision.retest_reason]
    session.add(
        RetestRecommendation(
            user_id=user_id,
            breakpoint_id=breakpoint_id,
            concept_id=target.target_id,
            skill_dimension_id=None,
            recommended_after=now,
            priority=priority,
            status="PENDING",
            strategy="DIFFERENT_CONTEXT",
            rationale=_retest_rationale(decision.retest_reason),
            generation_policy_version=policy_version,
            recommendation_key=recommendation_key,
            created_at=now,
            updated_at=now,
        )
    )


def _transition_key(
    user_id: UUID,
    target: MasteryTargetSource,
    previous_projection_version: int,
    from_state: str,
    to_state: str,
    before_evidence_ids: frozenset[UUID],
    after_evidence_ids: frozenset[UUID],
    policy_version: str,
) -> str:
    return _hash(
        "transition",
        str(user_id),
        target.family,
        str(target.target_id),
        str(previous_projection_version),
        from_state,
        to_state,
        policy_version,
        "before",
        *(sorted(str(item) for item in before_evidence_ids)),
        "after",
        *(sorted(str(item) for item in after_evidence_ids)),
    )


async def _monotonic_projection_time(
    session: AsyncSession,
    user_id: UUID,
    requested_now: datetime,
) -> datetime:
    timestamps: list[datetime] = [requested_now]
    timestamps.extend(
        await session.scalars(
            select(ConceptMastery.last_evaluated_at).where(
                ConceptMastery.user_id == user_id
            )
        )
    )
    timestamps.extend(
        await session.scalars(
            select(ConceptMastery.updated_at).where(ConceptMastery.user_id == user_id)
        )
    )
    timestamps.extend(
        await session.scalars(
            select(SkillMastery.last_evaluated_at).where(SkillMastery.user_id == user_id)
        )
    )
    timestamps.extend(
        await session.scalars(
            select(SkillMastery.updated_at).where(SkillMastery.user_id == user_id)
        )
    )
    return max(timestamps)


def _hash(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _retest_rationale(reason: RetestReason) -> str:
    return {
        RetestReason.UNRESOLVED_BREAKPOINT: (
            "A supported Breakpoint still needs an independent check in a different context."
        ),
        RetestReason.INDEPENDENCE_NOT_VERIFIED: (
            "Learning after guidance has not yet been verified independently."
        ),
        RetestReason.CONTRADICTORY_EVIDENCE: (
            "Performance is inconsistent across contexts and needs a clean verification."
        ),
        RetestReason.STALE_VERIFICATION: (
            "Strong understanding was demonstrated previously but has not been checked recently."
        ),
    }[reason]


async def _assert_work_claim(session: AsyncSession, claim: OutboxWorkClaim) -> None:
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.id == claim.outbox_event_id).with_for_update()
    )
    if not _owns_work_claim(event, claim.attempt):
        raise MasteryWorkOwnershipLost()


def _owns_work_claim(event: OutboxEvent | None, attempt: int) -> TypeGuard[OutboxEvent]:
    return bool(
        event is not None and event.attempt_count == attempt and event.status == "PROCESSING"
    )


def initial_mastery_recalculation_key(user_id: UUID, source_session_id: UUID) -> str:
    return f"mastery:{user_id}:{MASTERY_POLICY_VERSION}:session:{source_session_id}"

"""Deterministic candidate-safe Mastery view-model assembly."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from app.mastery.policy import (
    MASTERY_POLICY_VERSION,
    ContributionClassification,
    MasteryEvidenceContribution,
    MasteryPolicyV1,
    MasteryProjectionDecision,
    MasteryState,
)
from app.mastery.schema import (
    CandidateMasteryEvidenceItem,
    CandidateMasteryOverviewResponse,
    CandidateMasteryTarget,
    CandidateRetestRecommendation,
)
from app.mastery.source import MasterySourceBundle, MasteryTargetSource

_FIXTURE_RECOMMENDATION_NAMESPACE = UUID("8a000000-0000-4000-8000-000000000001")
_STATE_LABEL = {
    "UNTESTED": "Not tested",
    "EXPOSED": "Limited evidence",
    "WEAK": "Needs work",
    "DEVELOPING": "Developing",
    "STRONG": "Strong",
}
_SUFFICIENCY_LABEL = {
    "LOW": "Limited evidence",
    "MEDIUM": "Some evidence",
    "HIGH": "Well supported",
}
_FRESHNESS_LABEL = {
    "CURRENT": "Current",
    "AGING": "Not tested recently",
    "RETEST_DUE": "Retest due",
}


@dataclass(frozen=True, slots=True)
class PersistedMasteryProjection:
    family: Literal["CONCEPT", "SKILL"]
    target_id: UUID
    state: MasteryState
    mastery_policy_version: str
    projection_version: int
    last_evaluated_at: datetime
    last_evidence_at: datetime | None
    supporting_evidence_count: int
    context_diversity: int
    updated_at: datetime
    contributions: tuple[tuple[UUID, ContributionClassification, str], ...]


def build_persisted_candidate_mastery_overview(
    bundle: MasterySourceBundle,
    projections: tuple[PersistedMasteryProjection, ...],
    *,
    now: datetime | None = None,
    recommendation_ids: dict[tuple[str, UUID], UUID] | None = None,
    recommendation_statuses: dict[UUID, Literal["PENDING", "SCHEDULED"]] | None = None,
    status: str | None = None,
) -> CandidateMasteryOverviewResponse:
    """Render current persisted projections; policy evaluation is consistency-only."""

    evaluated_at = now or datetime.now(UTC)
    policy = MasteryPolicyV1()
    projection_by_target = {(item.family, item.target_id): item for item in projections}
    targets: list[CandidateMasteryTarget] = []
    decisions: dict[UUID, MasteryProjectionDecision] = {}
    consistency_mismatch = False

    for source in bundle.targets:
        projection = projection_by_target.get((source.family, source.target_id))
        if projection is None:
            if source.facts.evidence:
                consistency_mismatch = True
            continue
        recommendation_id = (
            recommendation_ids.get((source.family, source.target_id))
            if recommendation_ids is not None
            else None
        )
        persisted_contributions = tuple(
            MasteryEvidenceContribution(evidence_id, classification, context_key)
            for evidence_id, classification, context_key in projection.contributions
        )
        target_mismatch = False
        if projection.mastery_policy_version != policy.version:
            target_mismatch = True
            decision = _unknown_policy_detail(
                source,
                projection,
                persisted_contributions,
                recommendation_id=recommendation_id,
            )
        else:
            described = policy.describe_persisted(
                source.facts,
                persisted_state=projection.state,
                now=evaluated_at,
            )
            decision = replace(
                described,
                state=projection.state,
                contributions=persisted_contributions,
                supporting_evidence_ids=tuple(
                    evidence_id
                    for evidence_id, classification, _context_key in projection.contributions
                    if classification == "SUPPORTING"
                ),
                distinct_context_count=projection.context_diversity,
                last_evidence_at=projection.last_evidence_at,
            )
            expected = policy.evaluate(source.facts, now=evaluated_at)
            expected_contributions = {
                item.evidence_id: (item.classification, item.context_key)
                for item in expected.contributions
            }
            actual_contributions = {
                evidence_id: (classification, context_key)
                for evidence_id, classification, context_key in projection.contributions
            }
            target_mismatch = bool(
                expected.state != projection.state
                or expected.last_evidence_at != projection.last_evidence_at
                or len(expected.supporting_evidence_ids)
                != projection.supporting_evidence_count
                or expected.distinct_context_count != projection.context_diversity
                or expected_contributions != actual_contributions
            )
            if target_mismatch:
                decision = replace(
                    decision,
                    explanation_code="PROJECTION_STALE",
                    explanation=(
                        "This is the last persisted Mastery state. CounterQ is updating "
                        "its supporting detail from canonical Evidence."
                    ),
                )
        consistency_mismatch = consistency_mismatch or target_mismatch
        decisions[source.target_id] = decision
        targets.append(
            _target(
                source,
                decision,
                recommendation_id,
                projection=projection,
            )
        )

    technical = [
        item
        for item in targets
        if item.target_type == "CONCEPT" and item.state != "UNTESTED"
    ]
    skills = [
        item
        for item in targets
        if item.target_type == "SKILL" and item.state != "UNTESTED"
    ]
    parent_summaries = _parents(
        tuple(
            item
            for item in bundle.targets
            if (item.family, item.target_id) in projection_by_target
        ),
        decisions,
    )
    recommendations = _recommendations(
        technical,
        skills,
        recommendation_statuses=recommendation_statuses,
    )
    response_status = status or ("READY" if technical or skills else "EMPTY")
    if consistency_mismatch and response_status not in {"FAILED", "UPDATING"}:
        response_status = "STALE"
    versions = {item.mastery_policy_version for item in projections}
    if not versions:
        policy_version = MASTERY_POLICY_VERSION
    else:
        policy_version = (
            next(iter(versions)) if len(versions) == 1 else "mixed_projection_versions"
        )
    projection_updated_at = max(
        (item.updated_at for item in projections),
        default=evaluated_at,
    )
    return _overview_response(
        bundle=bundle,
        technical=technical,
        skills=skills,
        parent_summaries=parent_summaries,
        recommendations=recommendations,
        status=response_status,
        policy_version=policy_version,
        updated_at=projection_updated_at,
    )


def build_candidate_mastery_overview(
    bundle: MasterySourceBundle,
    *,
    now: datetime | None = None,
    projection_updated_at: datetime | None = None,
    mastery_policy_version: str = MASTERY_POLICY_VERSION,
    recommendation_ids: dict[tuple[str, UUID], UUID] | None = None,
    recommendation_statuses: dict[UUID, Literal["PENDING", "SCHEDULED"]] | None = None,
    status: str | None = None,
) -> CandidateMasteryOverviewResponse:
    evaluated_at = now or datetime.now(UTC)
    policy = MasteryPolicyV1()
    targets: list[CandidateMasteryTarget] = []
    decisions: dict[UUID, MasteryProjectionDecision] = {}
    for source in bundle.targets:
        decision = policy.evaluate(source.facts, now=evaluated_at)
        decisions[source.target_id] = decision
        recommendation_id = (
            recommendation_ids.get((source.family, source.target_id))
            if recommendation_ids is not None
            else None
        )
        if (
            recommendation_ids is None
            and source.family == "CONCEPT"
            and decision.retest_due
        ):
            recommendation_id = uuid5(
                _FIXTURE_RECOMMENDATION_NAMESPACE,
                f"{source.family}:{source.target_id}:{decision.retest_reason}",
            )
        targets.append(_target(source, decision, recommendation_id))

    technical = [
        item for item in targets if item.target_type == "CONCEPT" and item.state != "UNTESTED"
    ]
    skills = [item for item in targets if item.target_type == "SKILL" and item.state != "UNTESTED"]
    parent_summaries = _parents(bundle.targets, decisions)
    recommendations = _recommendations(
        technical,
        skills,
        recommendation_statuses=recommendation_statuses,
    )
    has_evidence = bool(technical or skills)
    response_status = status or ("READY" if has_evidence else "EMPTY")
    return _overview_response(
        bundle=bundle,
        technical=technical,
        skills=skills,
        parent_summaries=parent_summaries,
        recommendations=recommendations,
        status=response_status,
        policy_version=mastery_policy_version,
        updated_at=projection_updated_at or evaluated_at,
    )


def _overview_response(
    *,
    bundle: MasterySourceBundle,
    technical: list[CandidateMasteryTarget],
    skills: list[CandidateMasteryTarget],
    parent_summaries: list[CandidateMasteryTarget],
    recommendations: list[CandidateRetestRecommendation],
    status: str,
    policy_version: str,
    updated_at: datetime,
) -> CandidateMasteryOverviewResponse:
    has_evidence = bool(technical or skills)
    message = (
        "What CounterQ has learned from your evidence across interviews."
        if has_evidence
        else (
            "CounterQ is still learning where your interview strengths are. "
            "Complete a few interviews to build an evidence-backed view."
        )
    )
    if status == "UPDATING":
        message = "CounterQ is recalculating this view from your canonical evidence."
    elif status == "STALE":
        message = (
            "This view shows the last persisted Mastery state. CounterQ needs to "
            "refresh its supporting detail before treating it as current."
        )
    elif status == "FAILED":
        message = (
            "Mastery is temporarily unavailable. Your interview evidence, reports, and "
            "CounterMaps are unchanged."
        )
    return CandidateMasteryOverviewResponse(
        status=status,  # type: ignore[arg-type]
        user_id=bundle.user_id,
        mastery_policy_version=policy_version,
        target_level=bundle.target_level,
        updated_at=updated_at,
        message=message,
        technical_concepts=sorted(technical, key=_target_order),
        parent_summaries=sorted(parent_summaries, key=lambda item: item.display_name),
        interview_skills=sorted(skills, key=_target_order),
        retest_recommendations=sorted(
            recommendations, key=lambda item: (item.target_type, item.target_name)
        ),
    )


def _unknown_policy_detail(
    source: MasteryTargetSource,
    projection: PersistedMasteryProjection,
    contributions: tuple[MasteryEvidenceContribution, ...],
    *,
    recommendation_id: UUID | None,
) -> MasteryProjectionDecision:
    """Describe an unknown-policy row without running a different policy over it."""

    evidence = tuple(item for item in source.facts.evidence if item.valid)
    supporting = tuple(
        item.evidence_id for item in contributions if item.classification == "SUPPORTING"
    )
    contradicting = tuple(
        item.evidence_id for item in contributions if item.classification == "CONTRADICTING"
    )
    learning = tuple(
        item.evidence_id for item in contributions if item.classification == "LEARNING_LIMITED"
    )
    unresolved_breakpoints = tuple(
        item.breakpoint_id for item in source.facts.breakpoints if item.unresolved
    )
    retest_due = recommendation_id is not None or bool(unresolved_breakpoints)
    return MasteryProjectionDecision(
        state=projection.state,
        evidence_sufficiency="MEDIUM" if evidence else "LOW",
        freshness="RETEST_DUE" if retest_due else "CURRENT",
        explanation_code="POLICY_DETAIL_UPDATING",
        explanation=(
            "This is the last persisted Mastery state. CounterQ is updating its "
            "supporting detail for the current policy."
        ),
        contributions=contributions,
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting,
        learning_evidence_ids=learning,
        independent_demonstration_count=len(
            {
                item.observation_key
                for item in evidence
                if item.independence == "INDEPENDENT" and item.evidence_id in supporting
            }
        ),
        qualifying_positive_count=0,
        qualifying_negative_count=0,
        distinct_session_count=len({item.session_id for item in evidence}),
        distinct_problem_count=len(
            {item.problem_id or item.problem_version_id for item in evidence}
        ),
        distinct_context_count=projection.context_diversity,
        unresolved_breakpoint_ids=unresolved_breakpoints,
        retest_due=retest_due,
        retest_reason=None,
        last_evidence_at=projection.last_evidence_at,
    )


def _target(
    source: MasteryTargetSource,
    decision: MasteryProjectionDecision,
    recommendation_id: UUID | None,
    *,
    projection: PersistedMasteryProjection | None = None,
) -> CandidateMasteryTarget:
    contribution_by_id = {item.evidence_id: item.classification for item in decision.contributions}
    timeline = [
        CandidateMasteryEvidenceItem(
            evidence_id=item.evidence_id,
            contribution=contribution_by_id[item.evidence_id],
            recorded_at=item.occurred_at,
            problem=item.problem_title,
            mode=item.interview_mode,
            candidate_level=item.interview_level,
            polarity=item.polarity,
            strength=item.strength,
            independence=item.independence,
            retest_linked=item.is_retest,
            finding=item.finding,
            source_session_id=item.session_id,
        )
        for item in sorted(source.facts.evidence, key=lambda value: value.occurred_at, reverse=True)
        if item.valid
    ]
    return CandidateMasteryTarget(
        target_type=source.family,
        target_id=source.target_id,
        canonical_key=source.canonical_key,
        display_name=source.display_name,
        category=source.category,
        state=decision.state,
        state_label=_STATE_LABEL[decision.state],
        mastery_policy_version=(projection.mastery_policy_version if projection else None),
        projection_version=(projection.projection_version if projection else None),
        last_evidence_at=(projection.last_evidence_at if projection else None),
        supporting_evidence_count=(
            projection.supporting_evidence_count if projection else None
        ),
        context_diversity=(projection.context_diversity if projection else None),
        last_evaluated_at=(projection.last_evaluated_at if projection else None),
        projection_updated_at=(projection.updated_at if projection else None),
        evidence_sufficiency=decision.evidence_sufficiency,
        evidence_sufficiency_label=_SUFFICIENCY_LABEL[decision.evidence_sufficiency],
        freshness=decision.freshness,
        freshness_label=_FRESHNESS_LABEL[decision.freshness],
        reason=decision.explanation,
        evidence_count=len(timeline),
        distinct_session_count=decision.distinct_session_count,
        distinct_problem_count=decision.distinct_problem_count,
        distinct_context_count=decision.distinct_context_count,
        retest_due=decision.retest_due,
        recommendation_id=recommendation_id,
        next_action=_next_action(decision),
        unresolved_breakpoint_ids=list(decision.unresolved_breakpoint_ids),
        evidence=timeline,
        child_target_ids=[],
    )


def _parents(
    sources: tuple[MasteryTargetSource, ...],
    decisions: dict[UUID, MasteryProjectionDecision],
) -> list[CandidateMasteryTarget]:
    children: dict[UUID, list[MasteryTargetSource]] = defaultdict(list)
    for source in sources:
        if source.family == "CONCEPT" and source.parent_concept_id is not None:
            children[source.parent_concept_id].append(source)
    result = []
    for parent_id, group in children.items():
        tested = [item for item in group if decisions[item.target_id].state != "UNTESTED"]
        if not tested:
            continue
        states = {decisions[item.target_id].state for item in tested}
        state = _parent_state(states, len(tested))
        contexts = {
            fact.context_key for item in tested for fact in item.facts.evidence if fact.valid
        }
        sessions = {
            fact.session_id for item in tested for fact in item.facts.evidence if fact.valid
        }
        problems = {
            fact.problem_id or fact.problem_version_id
            for item in tested
            for fact in item.facts.evidence
            if fact.valid
        }
        evidence_ids = {
            fact.evidence_id for item in tested for fact in item.facts.evidence if fact.valid
        }
        parent_name = tested[0].parent_display_name or "Technical area"
        parent_key = tested[0].parent_canonical_key or f"parent-{parent_id}"
        result.append(
            CandidateMasteryTarget(
                target_type="PARENT_SUMMARY",
                target_id=parent_id,
                canonical_key=parent_key,
                display_name=parent_name,
                category=tested[0].category,
                state=state,
                state_label=_STATE_LABEL[state],
                evidence_sufficiency=_parent_sufficiency(tested, decisions),
                evidence_sufficiency_label=_SUFFICIENCY_LABEL[
                    _parent_sufficiency(tested, decisions)
                ],
                freshness=_parent_freshness(tested, decisions),
                freshness_label=_FRESHNESS_LABEL[_parent_freshness(tested, decisions)],
                reason=_parent_reason(states, len(tested)),
                evidence_count=len(evidence_ids),
                distinct_session_count=len(sessions),
                distinct_problem_count=len(problems),
                distinct_context_count=len(contexts),
                retest_due=any(decisions[item.target_id].retest_due for item in tested),
                recommendation_id=None,
                next_action="Open the child concepts to inspect the evidence behind this summary.",
                unresolved_breakpoint_ids=[
                    value
                    for item in tested
                    for value in decisions[item.target_id].unresolved_breakpoint_ids
                ],
                evidence=[],
                child_target_ids=[item.target_id for item in tested],
            )
        )
    return result


def _parent_state(states: set[MasteryState], tested_count: int) -> MasteryState:
    if tested_count == 1:
        return "DEVELOPING" if states.intersection({"STRONG", "DEVELOPING"}) else "EXPOSED"
    if "WEAK" in states and len(states) > 1:
        return "DEVELOPING"
    if states == {"WEAK"}:
        return "WEAK"
    if "DEVELOPING" in states or len(states) > 1:
        return "DEVELOPING"
    if states == {"STRONG"} and tested_count >= 2:
        return "STRONG"
    if states == {"STRONG"}:
        return "DEVELOPING"
    return "EXPOSED"


def _parent_sufficiency(
    tested: list[MasteryTargetSource],
    decisions: dict[UUID, MasteryProjectionDecision],
) -> Literal["LOW", "MEDIUM", "HIGH"]:
    values = [decisions[item.target_id].evidence_sufficiency for item in tested]
    if len(tested) >= 2 and all(item == "HIGH" for item in values):
        return "HIGH"
    if any(item in {"MEDIUM", "HIGH"} for item in values):
        return "MEDIUM"
    return "LOW"


def _parent_freshness(
    tested: list[MasteryTargetSource],
    decisions: dict[UUID, MasteryProjectionDecision],
) -> Literal["CURRENT", "AGING", "RETEST_DUE"]:
    rank = {"CURRENT": 0, "AGING": 1, "RETEST_DUE": 2}
    return max(
        (decisions[item.target_id].freshness for item in tested),
        key=rank.__getitem__,
    )


def _parent_reason(states: set[MasteryState], tested_count: int) -> str:
    if tested_count == 1:
        return (
            "Only one child concept has evidence, so CounterQ is not generalizing "
            "that result to the whole area."
        )
    if states == {"WEAK"}:
        return "Multiple child concepts show meaningful gaps that still need verification."
    if "WEAK" in states:
        return (
            "This area shows meaningful competence, but an important child concept still "
            "needs work."
        )
    if len(states) > 1:
        return "The child concepts are not yet consistent enough for a stronger area-level summary."
    return "This is a conservative summary of directly evidenced child concepts."


def _next_action(decision: MasteryProjectionDecision) -> str:
    if decision.retest_reason is not None:
        return {
            "UNRESOLVED_BREAKPOINT": (
                "Verify the unresolved gap independently in a different context."
            ),
            "INDEPENDENCE_NOT_VERIFIED": (
                "Verify this independently without repeating the taught prompt."
            ),
            "CONTRADICTORY_EVIDENCE": (
                "Use a clean independent context to resolve the inconsistency."
            ),
            "STALE_VERIFICATION": "Refresh this strong area with a different independent context.",
        }[decision.retest_reason.value]
    if decision.state == "WEAK":
        return "Revisit this gap in a clean independent context and show the reasoning holds."
    if decision.state == "DEVELOPING":
        return "Demonstrate this independently in another distinct context."
    if decision.state == "EXPOSED":
        return "Complete a meaningful independent attempt in this area."
    if decision.state == "STRONG":
        return "Keep the evidence current through future interviews."
    return "CounterQ needs meaningful evidence before recommending a next step."


def _recommendations(
    technical: list[CandidateMasteryTarget],
    skills: list[CandidateMasteryTarget],
    *,
    recommendation_statuses: dict[UUID, Literal["PENDING", "SCHEDULED"]] | None,
) -> list[CandidateRetestRecommendation]:
    return [
        CandidateRetestRecommendation(
            recommendation_id=item.recommendation_id,
            target_type=item.target_type,  # type: ignore[arg-type]
            target_id=item.target_id,
            target_name=item.display_name,
            status=(recommendation_statuses or {}).get(item.recommendation_id, "PENDING"),
            reason=item.next_action,
        )
        for item in [*technical, *skills]
        if item.retest_due and item.recommendation_id is not None
    ]


def _target_order(item: CandidateMasteryTarget) -> tuple[int, str]:
    rank = {"STRONG": 0, "DEVELOPING": 1, "WEAK": 2, "EXPOSED": 3, "UNTESTED": 4}
    return rank[item.state], item.display_name

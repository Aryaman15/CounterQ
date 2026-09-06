"""Deterministic candidate-safe Mastery view-model assembly."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from app.mastery.policy import (
    MASTERY_POLICY_VERSION,
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
    source_by_id: dict[UUID, MasteryTargetSource] = {}
    decisions: dict[UUID, MasteryProjectionDecision] = {}
    for source in bundle.targets:
        decision = policy.evaluate(source.facts, now=evaluated_at)
        source_by_id[source.target_id] = source
        decisions[source.target_id] = decision
        recommendation_id = (
            recommendation_ids.get((source.family, source.target_id))
            if recommendation_ids is not None
            else None
        )
        if recommendation_ids is None and decision.retest_due:
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
    recommendations = [
        CandidateRetestRecommendation(
            recommendation_id=item.recommendation_id,
            target_type=item.target_type,
            target_id=item.target_id,
            target_name=item.display_name,
            status=(recommendation_statuses or {}).get(item.recommendation_id, "PENDING"),
            reason=item.next_action,
        )
        for item in [*technical, *skills]
        if item.retest_due and item.recommendation_id is not None
    ]
    has_evidence = bool(technical or skills)
    response_status = status or ("READY" if has_evidence else "EMPTY")
    message = (
        "What CounterQ has evidence you can defend independently."
        if has_evidence
        else (
            "CounterQ is still learning where your interview strengths are. "
            "Complete a few interviews to build an evidence-backed view."
        )
    )
    if response_status == "UPDATING":
        message = "CounterQ is recalculating this view from your canonical evidence."
    elif response_status == "FAILED":
        message = (
            "Mastery is temporarily unavailable. Your interview evidence, reports, and "
            "CounterMaps are unchanged."
        )
    return CandidateMasteryOverviewResponse(
        status=response_status,
        user_id=bundle.user_id,
        mastery_policy_version=mastery_policy_version,
        target_level=bundle.target_level,
        updated_at=projection_updated_at or evaluated_at,
        message=message,
        technical_concepts=sorted(technical, key=_target_order),
        parent_summaries=sorted(parent_summaries, key=lambda item: item.display_name),
        interview_skills=sorted(skills, key=_target_order),
        retest_recommendations=sorted(
            recommendations, key=lambda item: (item.target_type, item.target_name)
        ),
    )


def _target(
    source: MasteryTargetSource,
    decision: MasteryProjectionDecision,
    recommendation_id: UUID | None,
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
            fact.problem_version_id for item in tested for fact in item.facts.evidence if fact.valid
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
                evidence_sufficiency="HIGH" if len(tested) >= 3 else "MEDIUM",
                evidence_sufficiency_label=(
                    _SUFFICIENCY_LABEL["HIGH"] if len(tested) >= 3 else _SUFFICIENCY_LABEL["MEDIUM"]
                ),
                freshness=(
                    "RETEST_DUE"
                    if any(decisions[item.target_id].freshness == "RETEST_DUE" for item in tested)
                    else "CURRENT"
                ),
                freshness_label=(
                    "Retest due"
                    if any(decisions[item.target_id].freshness == "RETEST_DUE" for item in tested)
                    else "Current"
                ),
                reason=_parent_reason(states),
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


def _parent_reason(states: set[MasteryState]) -> str:
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
    if decision.state == "DEVELOPING":
        return "Demonstrate this independently in another distinct context."
    if decision.state == "EXPOSED":
        return "Complete a meaningful independent attempt in this area."
    if decision.state == "STRONG":
        return "Keep the evidence current through future interviews."
    return "CounterQ needs meaningful evidence before recommending a next step."


def _target_order(item: CandidateMasteryTarget) -> tuple[int, str]:
    rank = {"STRONG": 0, "DEVELOPING": 1, "WEAK": 2, "EXPOSED": 3, "UNTESTED": 4}
    return rank[item.state], item.display_name

"""Deterministic Stage 8B retest selection and outcome policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.mastery.policy import MasteryEvidenceFact, RetestReason

RETEST_SELECTION_POLICY_VERSION = "retest_problem_selection_policy_v1"
RETEST_OUTCOME_POLICY_VERSION = "retest_outcome_policy_v1"

RetestOutcome = Literal["SATISFIED", "PERSISTED_GAP", "INCONCLUSIVE", "ABANDONED"]
RETEST_OUTCOMES: tuple[RetestOutcome, ...] = (
    "SATISFIED",
    "PERSISTED_GAP",
    "INCONCLUSIVE",
    "ABANDONED",
)

_RELEVANCE = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_ROLE = {"PRIMARY": 3, "SECONDARY": 2, "OPTIONAL": 1}
_MEANINGFUL = frozenset(("MODERATE", "STRONG"))


@dataclass(frozen=True, slots=True)
class RetestProblemCandidate:
    problem_id: UUID
    problem_version_id: UUID
    interview_pack_version_id: UUID
    slug: str
    title: str
    target_relevance: str
    target_role: str
    mapping_distance: int
    catalog_order: int
    supported_levels: frozenset[str]
    supported_languages: frozenset[str]
    structured_context: frozenset[str]


class RetestProblemSelectionPolicyV1:
    """Rank reviewed curated candidates without model or embedding input."""

    version = RETEST_SELECTION_POLICY_VERSION

    def select(
        self,
        candidates: tuple[RetestProblemCandidate, ...],
        *,
        target_level: str,
        language: str,
        most_recent_problem_id: UUID | None,
        most_recent_context: frozenset[str] = frozenset(),
    ) -> RetestProblemCandidate | None:
        eligible = tuple(
            item
            for item in candidates
            if target_level in item.supported_levels
            and language in item.supported_languages
            and item.problem_id != most_recent_problem_id
        )
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (
                -_RELEVANCE.get(item.target_relevance, 0),
                item.mapping_distance,
                -_ROLE.get(item.target_role, 0),
                item.structured_context == most_recent_context,
                item.catalog_order,
                item.slug,
                str(item.problem_id),
                str(item.problem_version_id),
            ),
        )


@dataclass(frozen=True, slots=True)
class RetestOutcomeDecision:
    outcome: RetestOutcome
    qualifying_positive_ids: tuple[UUID, ...]
    qualifying_negative_ids: tuple[UUID, ...]
    reason: str


class RetestOutcomePolicyV1:
    """Classify one completed attempt from its exact target Evidence only."""

    version = RETEST_OUTCOME_POLICY_VERSION

    def evaluate(
        self,
        evidence: tuple[MasteryEvidenceFact, ...],
        *,
        original_reason: RetestReason,
        session_abandoned: bool = False,
    ) -> RetestOutcomeDecision:
        if session_abandoned:
            return RetestOutcomeDecision("ABANDONED", (), (), "SESSION_ABANDONED")
        meaningful = tuple(
            item
            for item in evidence
            if item.valid
            and item.strength in _MEANINGFUL
            and item.demonstrates_reasoning_or_application
        )
        negatives = tuple(
            item.evidence_id
            for item in meaningful
            if item.polarity == "NEGATIVE" and item.independence == "INDEPENDENT"
        )
        if negatives:
            return RetestOutcomeDecision(
                "PERSISTED_GAP", (), negatives, "INDEPENDENT_GAP_PERSISTED"
            )
        positives = tuple(
            item.evidence_id
            for item in meaningful
            if item.independence == "INDEPENDENT"
            and (item.polarity == "POSITIVE" or item.is_self_correction)
        )
        if positives:
            return RetestOutcomeDecision(
                "SATISFIED", positives, (), f"{original_reason.value}_CANDIDATE_RESOLVED"
            )
        return RetestOutcomeDecision(
            "INCONCLUSIVE", (), (), "INSUFFICIENT_INDEPENDENT_TARGET_EVIDENCE"
        )


def recommendation_reason(recommendation_key: str) -> RetestReason:
    matches = tuple(reason for reason in RetestReason if f":{reason.value}:" in recommendation_key)
    if len(matches) != 1:
        raise ValueError("RetestRecommendation does not contain one controlled retest reason")
    return matches[0]

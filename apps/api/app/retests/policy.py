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
    structured_self_correction_ids: tuple[UUID, ...]
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
            return RetestOutcomeDecision("ABANDONED", (), (), (), "SESSION_ABANDONED")
        meaningful = tuple(
            item
            for item in evidence
            if item.valid
            and item.strength in _MEANINGFUL
            and item.demonstrates_reasoning_or_application
        )
        independent = tuple(item for item in meaningful if item.independence == "INDEPENDENT")
        negatives = tuple(
            item
            for item in independent
            if item.polarity == "NEGATIVE"
        )
        positives = tuple(
            item
            for item in independent
            if item.polarity == "POSITIVE"
        )
        self_corrections = tuple(
            item
            for item in independent
            if item.is_self_correction and item.polarity in {"POSITIVE", "MIXED"}
        )

        if not negatives:
            satisfying = _ordered_unique((*positives, *self_corrections))
            if satisfying:
                return RetestOutcomeDecision(
                    "SATISFIED",
                    tuple(item.evidence_id for item in satisfying),
                    (),
                    tuple(item.evidence_id for item in self_corrections),
                    f"{original_reason.value}_CANDIDATE_RESOLVED",
                )
            return RetestOutcomeDecision(
                "INCONCLUSIVE",
                (),
                (),
                (),
                "INSUFFICIENT_INDEPENDENT_TARGET_EVIDENCE",
            )

        negative_ids = tuple(item.evidence_id for item in negatives)
        if not self_corrections:
            if positives:
                return RetestOutcomeDecision(
                    "INCONCLUSIVE",
                    (),
                    (),
                    (),
                    "CONTRADICTORY_EVIDENCE_WITHOUT_STRUCTURED_CORRECTION",
                )
            return RetestOutcomeDecision(
                "PERSISTED_GAP",
                (),
                negative_ids,
                (),
                "INDEPENDENT_GAP_PERSISTED",
            )

        if any(
            negative.occurred_at == correction.occurred_at
            for negative in negatives
            for correction in self_corrections
        ):
            return RetestOutcomeDecision(
                "INCONCLUSIVE", (), (), (), "SELF_CORRECTION_CHRONOLOGY_AMBIGUOUS"
            )

        latest_correction_at = max(item.occurred_at for item in self_corrections)
        if any(item.occurred_at > latest_correction_at for item in negatives):
            return RetestOutcomeDecision(
                "PERSISTED_GAP",
                (),
                negative_ids,
                (),
                "GAP_RECURRED_AFTER_SELF_CORRECTION",
            )

        distinct_negative_observations = {item.observation_key for item in negatives}
        if len(distinct_negative_observations) != 1:
            return RetestOutcomeDecision(
                "INCONCLUSIVE", (), (), (), "REPEATED_GAPS_WITH_SELF_CORRECTION"
            )

        latest_negative_at = max(item.occurred_at for item in negatives)
        later_corrections = tuple(
            item for item in self_corrections if item.occurred_at > latest_negative_at
        )
        if later_corrections:
            return RetestOutcomeDecision(
                "SATISFIED",
                tuple(item.evidence_id for item in later_corrections),
                (),
                tuple(item.evidence_id for item in later_corrections),
                f"{original_reason.value}_INDEPENDENT_SELF_CORRECTION",
            )
        return RetestOutcomeDecision(
            "INCONCLUSIVE", (), (), (), "SELF_CORRECTION_CHRONOLOGY_UNPROVED"
        )


def _ordered_unique(
    evidence: tuple[MasteryEvidenceFact, ...],
) -> tuple[MasteryEvidenceFact, ...]:
    by_id = {item.evidence_id: item for item in evidence}
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (item.occurred_at, str(item.evidence_id)),
        )
    )


def recommendation_reason(recommendation_key: str) -> RetestReason:
    matches = tuple(reason for reason in RetestReason if f":{reason.value}:" in recommendation_key)
    if len(matches) != 1:
        raise ValueError("RetestRecommendation does not contain one controlled retest reason")
    return matches[0]

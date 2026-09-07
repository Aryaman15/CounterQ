"""Versioned deterministic Mastery policy over normalized canonical Evidence facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

MASTERY_POLICY_VERSION = "mastery_policy_v1"

# Verification freshness is deliberately separate from categorical mastery.
CURRENT_WINDOW = timedelta(days=90)
AGING_WINDOW = timedelta(days=180)

MasteryTargetFamily = Literal["CONCEPT", "SKILL"]
MasteryState = Literal["UNTESTED", "EXPOSED", "WEAK", "DEVELOPING", "STRONG"]
EvidenceSufficiency = Literal["LOW", "MEDIUM", "HIGH"]
VerificationFreshness = Literal["CURRENT", "AGING", "RETEST_DUE"]
ContributionClassification = Literal["SUPPORTING", "CONTRADICTING", "LEARNING_LIMITED"]
EvidencePolarity = Literal["POSITIVE", "NEGATIVE", "MIXED"]
EvidenceStrength = Literal["WEAK", "MODERATE", "STRONG"]
EvidenceIndependence = Literal[
    "INDEPENDENT",
    "AFTER_PROBE",
    "AFTER_LIGHT_GUIDANCE",
    "AFTER_STRONG_HINT",
    "DIRECTLY_TAUGHT",
]
InterviewMode = Literal["COACH", "SIMULATION"]
InterviewLevel = Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
BreakpointStatus = Literal["OPEN", "RETEST_PENDING", "IMPROVING", "RESOLVED", "DISMISSED"]

_QUALIFYING_INDEPENDENCE = frozenset(("INDEPENDENT", "AFTER_PROBE"))
_MEANINGFUL_STRENGTH = frozenset(("MODERATE", "STRONG"))
_LEVEL_ORDER = {"INTERN": 0, "NEW_GRAD": 1, "EARLY_CAREER": 2}


class RetestReason(StrEnum):
    UNRESOLVED_BREAKPOINT = "UNRESOLVED_BREAKPOINT"
    INDEPENDENCE_NOT_VERIFIED = "INDEPENDENCE_NOT_VERIFIED"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    STALE_VERIFICATION = "STALE_VERIFICATION"


@dataclass(frozen=True, slots=True)
class MasteryEvidenceFact:
    evidence_id: UUID
    session_id: UUID
    problem_version_id: UUID
    occurred_at: datetime
    polarity: EvidencePolarity
    strength: EvidenceStrength
    independence: EvidenceIndependence
    evidence_type: str
    interview_mode: InterviewMode
    interview_level: InterviewLevel
    context_key: str
    observation_key: str
    concept_family_key: str
    problem_id: UUID | None = None
    demonstration_form: str = "UNSPECIFIED"
    prompt_kind: str | None = None
    probe_strategy: str | None = None
    is_retest: bool = False
    is_self_correction: bool = False
    demonstrates_reasoning_or_application: bool = True
    valid: bool = True
    problem_title: str = "Interview problem"
    finding: str = "Canonical evidence was recorded for this area."

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("Mastery Evidence timestamps must be timezone-aware")
        if not self.context_key or not self.observation_key or not self.concept_family_key:
            raise ValueError("Mastery Evidence context identities must be non-empty")


@dataclass(frozen=True, slots=True)
class MasteryBreakpointFact:
    breakpoint_id: UUID
    status: BreakpointStatus
    supporting_evidence_ids: frozenset[UUID]

    @property
    def unresolved(self) -> bool:
        return self.status in {"OPEN", "RETEST_PENDING", "IMPROVING"}


@dataclass(frozen=True, slots=True)
class MasteryTargetFacts:
    target_family: MasteryTargetFamily
    target_level: InterviewLevel
    evidence: tuple[MasteryEvidenceFact, ...]
    breakpoints: tuple[MasteryBreakpointFact, ...] = ()


@dataclass(frozen=True, slots=True)
class MasteryEvidenceContribution:
    evidence_id: UUID
    classification: ContributionClassification
    context_key: str


@dataclass(frozen=True, slots=True)
class MasteryProjectionDecision:
    state: MasteryState
    evidence_sufficiency: EvidenceSufficiency
    freshness: VerificationFreshness
    explanation_code: str
    explanation: str
    contributions: tuple[MasteryEvidenceContribution, ...]
    supporting_evidence_ids: tuple[UUID, ...]
    contradicting_evidence_ids: tuple[UUID, ...]
    learning_evidence_ids: tuple[UUID, ...]
    independent_demonstration_count: int
    qualifying_positive_count: int
    qualifying_negative_count: int
    distinct_session_count: int
    distinct_problem_count: int
    distinct_context_count: int
    unresolved_breakpoint_ids: tuple[UUID, ...]
    retest_due: bool
    retest_reason: RetestReason | None
    last_evidence_at: datetime | None


class MasteryPolicyV1:
    """Conservative rule policy; no scores, learned weights, or model decisions."""

    version = MASTERY_POLICY_VERSION

    def evaluate(
        self,
        facts: MasteryTargetFacts,
        *,
        now: datetime | None = None,
    ) -> MasteryProjectionDecision:
        return self._evaluate(facts, now=now, persisted_state=None)

    def describe_persisted(
        self,
        facts: MasteryTargetFacts,
        *,
        persisted_state: MasteryState,
        now: datetime | None = None,
    ) -> MasteryProjectionDecision:
        """Derive detail without selecting a replacement categorical state."""

        return self._evaluate(facts, now=now, persisted_state=persisted_state)

    def _evaluate(
        self,
        facts: MasteryTargetFacts,
        *,
        now: datetime | None,
        persisted_state: MasteryState | None,
    ) -> MasteryProjectionDecision:
        evaluated_at = now or datetime.now(UTC)
        if evaluated_at.tzinfo is None:
            raise ValueError("Mastery policy clock must be timezone-aware")
        evidence = tuple(sorted((item for item in facts.evidence if item.valid), key=_fact_order))
        contributions = tuple(_contribution(item) for item in evidence)
        supporting = tuple(
            item.evidence_id for item in contributions if item.classification == "SUPPORTING"
        )
        contradicting = tuple(
            item.evidence_id for item in contributions if item.classification == "CONTRADICTING"
        )
        learning = tuple(
            item.evidence_id for item in contributions if item.classification == "LEARNING_LIMITED"
        )
        meaningful_positive = tuple(item for item in evidence if _qualifying_positive(item))
        meaningful_negative = tuple(item for item in evidence if _qualifying_negative(item))
        meaningful_self_corrections = tuple(
            item for item in evidence if _meaningful_self_correction(item)
        )
        unresolved_negative = tuple(
            item
            for item in meaningful_negative
            if not _negative_overcome(item, meaningful_positive)
        )
        unresolved_breakpoints = tuple(item for item in facts.breakpoints if item.unresolved)
        distinct_sessions = len({item.session_id for item in evidence})
        distinct_problems = len(
            {item.problem_id or item.problem_version_id for item in evidence}
        )
        distinct_contexts = len({item.context_key for item in evidence})
        independent_count = len(
            {
                (item.session_id, item.context_key)
                for item in meaningful_positive
                if item.independence == "INDEPENDENT"
            }
        )

        state = persisted_state or self._state(
            facts=facts,
            evidence=evidence,
            positives=meaningful_positive,
            negatives=meaningful_negative,
            self_corrections=meaningful_self_corrections,
            unresolved_negatives=unresolved_negative,
            unresolved_breakpoints=unresolved_breakpoints,
        )
        sufficiency = _sufficiency(
            evidence,
            distinct_sessions=distinct_sessions,
            distinct_contexts=distinct_contexts,
        )
        freshness, retest_reason = _freshness(
            evidence=evidence,
            state=state,
            unresolved_negatives=unresolved_negative,
            unresolved_breakpoints=unresolved_breakpoints,
            now=evaluated_at,
        )
        retest_due = retest_reason is not None
        explanation_code, explanation = _explanation(
            state=state,
            freshness=freshness,
            has_positive=bool(meaningful_positive),
            has_negative=bool(meaningful_negative),
            has_learning=bool(learning),
            independent_count=independent_count,
            after_probe_count=sum(
                item.independence == "AFTER_PROBE" for item in meaningful_positive
            ),
            has_independent_negative=any(
                item.independence == "INDEPENDENT" for item in unresolved_negative
            ),
            has_self_correction=bool(meaningful_self_corrections),
        )
        return MasteryProjectionDecision(
            state=state,
            evidence_sufficiency=sufficiency,
            freshness=freshness,
            explanation_code=explanation_code,
            explanation=explanation,
            contributions=contributions,
            supporting_evidence_ids=supporting,
            contradicting_evidence_ids=contradicting,
            learning_evidence_ids=learning,
            independent_demonstration_count=independent_count,
            qualifying_positive_count=len(meaningful_positive),
            qualifying_negative_count=len(meaningful_negative),
            distinct_session_count=distinct_sessions,
            distinct_problem_count=distinct_problems,
            distinct_context_count=distinct_contexts,
            unresolved_breakpoint_ids=tuple(item.breakpoint_id for item in unresolved_breakpoints),
            retest_due=retest_due,
            retest_reason=retest_reason,
            last_evidence_at=evidence[-1].occurred_at if evidence else None,
        )

    def _state(
        self,
        *,
        facts: MasteryTargetFacts,
        evidence: tuple[MasteryEvidenceFact, ...],
        positives: tuple[MasteryEvidenceFact, ...],
        negatives: tuple[MasteryEvidenceFact, ...],
        self_corrections: tuple[MasteryEvidenceFact, ...],
        unresolved_negatives: tuple[MasteryEvidenceFact, ...],
        unresolved_breakpoints: tuple[MasteryBreakpointFact, ...],
    ) -> MasteryState:
        if not evidence:
            return "UNTESTED"

        # A failed independent retest is deliberately highly diagnostic.
        if any(item.is_retest and item.strength == "STRONG" for item in unresolved_negatives):
            return "WEAK"

        repeated_misconception = len(
            {item.observation_key for item in unresolved_negatives}
        ) >= 2
        if repeated_misconception and self_corrections and not positives:
            return "WEAK"

        isolated_corrected_negative = _isolated_self_correction(
            negatives, self_corrections
        )
        material_contradiction = bool(positives and unresolved_negatives)
        if material_contradiction or isolated_corrected_negative:
            return "DEVELOPING"

        if _eligible_strong(facts, positives, unresolved_negatives, unresolved_breakpoints):
            return "STRONG"

        if positives or self_corrections:
            return "DEVELOPING"

        negative_contexts = {item.observation_key for item in unresolved_negatives}
        highly_diagnostic = any(item.strength == "STRONG" for item in unresolved_negatives)
        aligned_moderate = len(negative_contexts) >= 2
        breakpoint_supported = any(
            set(item.supporting_evidence_ids)
            & {negative.evidence_id for negative in unresolved_negatives}
            for item in unresolved_breakpoints
        )
        if highly_diagnostic or aligned_moderate or breakpoint_supported:
            return "WEAK"
        return "EXPOSED"


def _fact_order(item: MasteryEvidenceFact) -> tuple[datetime, str]:
    return item.occurred_at, str(item.evidence_id)


def _qualifying_positive(item: MasteryEvidenceFact) -> bool:
    return bool(
        item.polarity == "POSITIVE"
        and item.strength in _MEANINGFUL_STRENGTH
        and item.independence in _QUALIFYING_INDEPENDENCE
        and item.demonstrates_reasoning_or_application
    )


def _qualifying_negative(item: MasteryEvidenceFact) -> bool:
    return bool(
        item.polarity == "NEGATIVE"
        and item.strength in _MEANINGFUL_STRENGTH
        and item.independence in _QUALIFYING_INDEPENDENCE
        and item.demonstrates_reasoning_or_application
    )


def _meaningful_self_correction(item: MasteryEvidenceFact) -> bool:
    return bool(
        item.is_self_correction
        and item.polarity in {"POSITIVE", "MIXED"}
        and item.strength in _MEANINGFUL_STRENGTH
        and item.independence == "INDEPENDENT"
        and item.demonstrates_reasoning_or_application
    )


def _contribution(item: MasteryEvidenceFact) -> MasteryEvidenceContribution:
    if _qualifying_positive(item) or _meaningful_self_correction(item):
        classification: ContributionClassification = "SUPPORTING"
    elif _qualifying_negative(item):
        classification = "CONTRADICTING"
    else:
        classification = "LEARNING_LIMITED"
    return MasteryEvidenceContribution(item.evidence_id, classification, item.context_key)


def _negative_overcome(
    negative: MasteryEvidenceFact,
    positives: tuple[MasteryEvidenceFact, ...],
) -> bool:
    later = tuple(item for item in positives if item.occurred_at > negative.occurred_at)
    return bool(
        len({item.context_key for item in later}) >= 2
        and len({item.session_id for item in later}) >= 2
        and any(item.independence == "INDEPENDENT" for item in later)
    )


def _isolated_self_correction(
    negatives: tuple[MasteryEvidenceFact, ...],
    self_corrections: tuple[MasteryEvidenceFact, ...],
) -> bool:
    if len(negatives) != 1:
        return False
    return any(item.occurred_at >= negatives[0].occurred_at for item in self_corrections)


def _eligible_strong(
    facts: MasteryTargetFacts,
    positives: tuple[MasteryEvidenceFact, ...],
    unresolved_negatives: tuple[MasteryEvidenceFact, ...],
    unresolved_breakpoints: tuple[MasteryBreakpointFact, ...],
) -> bool:
    if unresolved_negatives or unresolved_breakpoints:
        return False
    if len(positives) < 2:
        return False
    if len({item.session_id for item in positives}) < 2:
        return False
    if len({item.context_key for item in positives}) < 2:
        return False
    if not any(item.independence == "INDEPENDENT" for item in positives):
        return False
    if not any(item.independence in _QUALIFYING_INDEPENDENCE for item in positives[1:]):
        return False
    target_level = _LEVEL_ORDER[facts.target_level]
    if not any(_LEVEL_ORDER[item.interview_level] >= target_level for item in positives):
        return False
    if facts.target_family == "SKILL":
        if len({item.problem_id or item.problem_version_id for item in positives}) < 2:
            return False
        if len({item.concept_family_key for item in positives}) < 2:
            return False
    return True


def _sufficiency(
    evidence: tuple[MasteryEvidenceFact, ...],
    *,
    distinct_sessions: int,
    distinct_contexts: int,
) -> EvidenceSufficiency:
    meaningful = tuple(
        item
        for item in evidence
        if item.strength in _MEANINGFUL_STRENGTH
        and (item.independence in _QUALIFYING_INDEPENDENCE or item.polarity == "MIXED")
    )
    diagnostic_units = {
        (item.observation_key, item.demonstration_form) for item in meaningful
    }
    if len(diagnostic_units) >= 3 and (
        distinct_contexts >= 2 or distinct_sessions == 1
    ):
        return "HIGH"
    if len(diagnostic_units) >= 2 or any(
        item.strength == "STRONG" and item.independence in _QUALIFYING_INDEPENDENCE
        for item in meaningful
    ):
        return "MEDIUM"
    return "LOW"


def _freshness(
    *,
    evidence: tuple[MasteryEvidenceFact, ...],
    state: MasteryState,
    unresolved_negatives: tuple[MasteryEvidenceFact, ...],
    unresolved_breakpoints: tuple[MasteryBreakpointFact, ...],
    now: datetime,
) -> tuple[VerificationFreshness, RetestReason | None]:
    if not evidence:
        return "CURRENT", None
    if unresolved_breakpoints:
        return "RETEST_DUE", RetestReason.UNRESOLVED_BREAKPOINT
    has_learning = any(
        item.independence in {"AFTER_STRONG_HINT", "DIRECTLY_TAUGHT"} for item in evidence
    )
    last_learning_at = max(
        (
            item.occurred_at
            for item in evidence
            if item.independence in {"AFTER_STRONG_HINT", "DIRECTLY_TAUGHT"}
        ),
        default=None,
    )
    independently_verified_after = bool(
        last_learning_at
        and any(
            _qualifying_positive(item)
            and item.independence == "INDEPENDENT"
            and item.occurred_at > last_learning_at
            for item in evidence
        )
    )
    if has_learning and not independently_verified_after:
        return "RETEST_DUE", RetestReason.INDEPENDENCE_NOT_VERIFIED
    if unresolved_negatives and any(_qualifying_positive(item) for item in evidence):
        return "RETEST_DUE", RetestReason.CONTRADICTORY_EVIDENCE
    last_verification = max(
        (item.occurred_at for item in evidence if _qualifying_positive(item)),
        default=evidence[-1].occurred_at,
    )
    age = now - last_verification
    if age >= AGING_WINDOW:
        return "RETEST_DUE", RetestReason.STALE_VERIFICATION
    if age >= CURRENT_WINDOW:
        return "AGING", None
    # State is deliberately unused here: time never directly weakens categorical mastery.
    _ = state
    return "CURRENT", None


def _explanation(
    *,
    state: MasteryState,
    freshness: VerificationFreshness,
    has_positive: bool,
    has_negative: bool,
    has_learning: bool,
    independent_count: int,
    after_probe_count: int,
    has_independent_negative: bool,
    has_self_correction: bool,
) -> tuple[str, str]:
    if state == "UNTESTED":
        return "NO_EVIDENCE", "CounterQ does not yet have meaningful evidence for this area."
    if state == "EXPOSED":
        return (
            "LIMITED_DIRECTIONAL_EVIDENCE",
            "CounterQ has seen this area, but not enough trustworthy evidence to judge it yet.",
        )
    if state == "WEAK":
        if not has_independent_negative:
            return (
                "DIAGNOSTIC_GAP",
                "Diagnostic evidence shows a meaningful gap that still needs verification.",
            )
        return (
            "DIAGNOSTIC_GAP",
            "Independent evidence shows a meaningful gap that still needs verification.",
        )
    if has_positive and has_negative:
        return (
            "INCONSISTENT_CONTEXTS",
            "You have meaningful positive evidence, but your performance has been "
            "inconsistent across contexts.",
        )
    if has_learning and independent_count == 0:
        return (
            "GUIDANCE_NOT_VERIFIED",
            "You improved after guidance, but CounterQ has not yet seen you verify this "
            "independently.",
        )
    if state == "DEVELOPING" and has_self_correction:
        return (
            "INDEPENDENT_SELF_CORRECTION",
            "You corrected this independently. CounterQ still needs another distinct "
            "context before calling it Strong.",
        )
    if state == "DEVELOPING":
        if independent_count == 0 and after_probe_count > 0:
            return (
                "AFTER_PROBE_DEMONSTRATION",
                "You demonstrated this after a diagnostic challenge. CounterQ still needs "
                "a fully independent context before calling it Strong.",
            )
        return (
            "ANOTHER_CONTEXT_NEEDED",
            "You demonstrated this independently once. CounterQ needs another independent "
            "context before calling it Strong.",
        )
    if state == "STRONG" and freshness == "RETEST_DUE":
        return (
            "STRONG_BUT_STALE",
            "You demonstrated this strongly across multiple contexts, but it has not been "
            "verified recently.",
        )
    return (
        "REPEATED_INDEPENDENT_EVIDENCE",
        "You demonstrated this independently across multiple distinct contexts.",
    )

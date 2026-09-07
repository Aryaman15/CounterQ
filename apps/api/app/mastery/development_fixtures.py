"""Canonical Evidence scenarios evaluated by the production Mastery policy and view builder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.mastery.policy import (
    EvidenceIndependence,
    EvidencePolarity,
    EvidenceStrength,
    InterviewMode,
    MasteryBreakpointFact,
    MasteryEvidenceFact,
    MasteryTargetFacts,
)
from app.mastery.source import MasterySourceBundle, MasteryTargetSource

DEMO_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
DEMO_USER_ID = UUID("8a000000-0000-4000-8000-000000000010")
SLIDING_WINDOW_ID = UUID("8a000000-0000-4000-8000-000000000100")
WINDOW_VALIDITY_ID = UUID("8a000000-0000-4000-8000-000000000101")
BOUNDARY_MONOTONICITY_ID = UUID("8a000000-0000-4000-8000-000000000102")
STATE_MAINTENANCE_ID = UUID("8a000000-0000-4000-8000-000000000103")
HASH_COMPLEXITY_ID = UUID("8a000000-0000-4000-8000-000000000110")
COMPLEXITY_SKILL_ID = UUID("8a000000-0000-4000-8000-000000000200")


@dataclass(frozen=True, slots=True)
class DevelopmentMasteryFixture:
    fixture_id: str
    label: str
    description: str
    bundle: MasterySourceBundle


def load_development_mastery_fixtures() -> tuple[DevelopmentMasteryFixture, ...]:
    return (
        DevelopmentMasteryFixture(
            "cold-start",
            "Cold start",
            "Sparse by design: neutral defaults and no full untested ontology.",
            MasterySourceBundle(DEMO_USER_ID, "NEW_GRAD", ()),
        ),
        DevelopmentMasteryFixture(
            "one-independent-session",
            "One strong session",
            "A strong independent demonstration remains Developing until another "
            "context verifies it.",
            _one_session(),
        ),
        DevelopmentMasteryFixture(
            "mixed-coach-retest-due",
            "Coach + retest due",
            "Pre-assistance negative evidence remains visible beside taught improvement "
            "and an unresolved Breakpoint.",
            _mixed_coach(),
        ),
        DevelopmentMasteryFixture(
            "multi-context-strong",
            "Multi-context strong",
            "Repeated independent evidence earns Strong while mixed child and skill "
            "evidence stays Developing.",
            _multi_context(),
        ),
        DevelopmentMasteryFixture(
            "strong-but-stale",
            "Strong, retest due",
            "Time changes verification freshness, never historical Mastery state by itself.",
            _stale_strong(),
        ),
    )


def _one_session() -> MasterySourceBundle:
    evidence = (
        _fact(
            1,
            session=1,
            problem=1,
            days_ago=5,
            polarity="POSITIVE",
            strength="STRONG",
            independence="INDEPENDENT",
            context="longest-substring:invariant-defense",
            family="sliding_window",
            problem_title="Longest Substring Without Repeating Characters",
            finding="Independently defended why the active window boundary never moves backward.",
        ),
    )
    return _bundle(
        (
            _concept(
                BOUNDARY_MONOTONICITY_ID,
                "sliding_window_boundary_monotonicity",
                "Boundary monotonicity",
                evidence,
            ),
        )
    )


def _mixed_coach() -> MasterySourceBundle:
    negative = _fact(
        10,
        session=2,
        problem=2,
        days_ago=14,
        polarity="NEGATIVE",
        strength="STRONG",
        independence="INDEPENDENT",
        context="two-sum:worst-case-defense",
        family="hashing",
        problem_title="Two Sum",
        finding="Treated hash lookup as a guaranteed worst-case constant-time operation.",
    )
    taught = _fact(
        11,
        session=2,
        problem=2,
        days_ago=14,
        polarity="POSITIVE",
        strength="MODERATE",
        independence="DIRECTLY_TAUGHT",
        context="two-sum:taught-collision-behavior",
        family="hashing",
        mode="COACH",
        problem_title="Two Sum",
        finding="Explained collision degradation after direct teaching.",
    )
    breakpoint = MasteryBreakpointFact(
        UUID("8a000000-0000-4000-8000-000000000301"),
        "RETEST_PENDING",
        "HIGH",
        DEMO_NOW - timedelta(days=14),
        frozenset((negative.evidence_id,)),
    )
    target = _concept(
        HASH_COMPLEXITY_ID,
        "hash_table_complexity",
        "Hash table complexity",
        (negative, taught),
        parent_id=None,
        parent_key=None,
        parent_name=None,
        breakpoints=(breakpoint,),
        category="HASHING",
    )
    return _bundle((target,))


def _multi_context() -> MasterySourceBundle:
    boundary = (
        _fact(
            20,
            session=3,
            problem=3,
            days_ago=28,
            polarity="POSITIVE",
            strength="STRONG",
            independence="INDEPENDENT",
            context="longest-substring:boundary-defense",
            family="sliding_window",
            problem_title="Longest Substring Without Repeating Characters",
            finding=(
                "Independently defended a monotonic left boundary and implemented it correctly."
            ),
        ),
        _fact(
            21,
            session=4,
            problem=4,
            days_ago=3,
            polarity="POSITIVE",
            strength="STRONG",
            independence="AFTER_PROBE",
            context="minimum-subarray:constraint-transfer",
            family="sliding_window",
            problem_title="Minimum Size Subarray Sum",
            finding=(
                "Preserved boundary monotonicity under a different window constraint "
                "after a diagnostic probe."
            ),
        ),
    )
    validity = (
        _fact(
            22,
            session=5,
            problem=5,
            days_ago=2,
            polarity="NEGATIVE",
            strength="STRONG",
            independence="AFTER_PROBE",
            context="minimum-window:validity-counterexample",
            family="sliding_window",
            problem_title="Minimum Window Variant",
            finding=(
                "Could not defend when shrinking preserves the required window validity condition."
            ),
        ),
    )
    maintenance = (
        _fact(
            23,
            session=4,
            problem=4,
            days_ago=3,
            polarity="POSITIVE",
            strength="MODERATE",
            independence="INDEPENDENT",
            context="minimum-subarray:state-update",
            family="sliding_window",
            problem_title="Minimum Size Subarray Sum",
            finding=(
                "Maintained the running window state correctly in one independent implementation."
            ),
        ),
    )
    skill_evidence = (
        _fact(
            24,
            session=3,
            problem=3,
            days_ago=28,
            polarity="POSITIVE",
            strength="STRONG",
            independence="INDEPENDENT",
            context="two-pointers:amortized-analysis",
            family="two_pointers",
            problem_title="Container With Most Water",
            finding="Correctly derived amortized linear pointer movement.",
        ),
        _fact(
            25,
            session=6,
            problem=6,
            days_ago=10,
            polarity="NEGATIVE",
            strength="MODERATE",
            independence="INDEPENDENT",
            context="hashing:average-worst-case",
            family="hashing",
            problem_title="Top K Frequent Elements",
            finding="Used expected hash-table lookup as an unconditional guarantee.",
        ),
        _fact(
            26,
            session=7,
            problem=7,
            days_ago=1,
            polarity="POSITIVE",
            strength="STRONG",
            independence="AFTER_PROBE",
            context="recursion:stack-space",
            family="recursion",
            problem_title="Graph Traversal",
            finding=(
                "Included recursion depth in auxiliary-space reasoning after a diagnostic probe."
            ),
        ),
    )
    return _bundle(
        (
            _concept(
                BOUNDARY_MONOTONICITY_ID,
                "sliding_window_boundary_monotonicity",
                "Boundary monotonicity",
                boundary,
            ),
            _concept(
                WINDOW_VALIDITY_ID, "sliding_window_window_validity", "Window validity", validity
            ),
            _concept(
                STATE_MAINTENANCE_ID,
                "sliding_window_state_maintenance",
                "State maintenance",
                maintenance,
            ),
            _skill(
                COMPLEXITY_SKILL_ID, "complexity_reasoning", "Complexity reasoning", skill_evidence
            ),
        )
    )


def _stale_strong() -> MasterySourceBundle:
    evidence = (
        _fact(
            30,
            session=8,
            problem=8,
            days_ago=230,
            polarity="POSITIVE",
            strength="STRONG",
            independence="INDEPENDENT",
            context="two-sum:collision-analysis",
            family="hashing",
            problem_title="Two Sum",
            finding="Independently distinguished expected and worst-case hash-table lookup.",
        ),
        _fact(
            31,
            session=9,
            problem=9,
            days_ago=200,
            polarity="POSITIVE",
            strength="STRONG",
            independence="AFTER_PROBE",
            context="contains-duplicate:guarantee-transfer",
            family="hashing",
            problem_title="Contains Duplicate",
            finding="Transferred collision reasoning to a different hash-backed algorithm.",
        ),
    )
    return _bundle(
        (
            _concept(
                HASH_COMPLEXITY_ID,
                "hash_table_complexity",
                "Hash table complexity",
                evidence,
                parent_id=None,
                parent_key=None,
                parent_name=None,
                category="HASHING",
            ),
        )
    )


def _bundle(targets: tuple[MasteryTargetSource, ...]) -> MasterySourceBundle:
    return MasterySourceBundle(DEMO_USER_ID, "NEW_GRAD", targets)


def _concept(
    target_id: UUID,
    key: str,
    name: str,
    evidence: tuple[MasteryEvidenceFact, ...],
    *,
    parent_id: UUID | None = SLIDING_WINDOW_ID,
    parent_key: str | None = "sliding_window",
    parent_name: str | None = "Sliding Window",
    breakpoints: tuple[MasteryBreakpointFact, ...] = (),
    category: str = "ALGORITHM",
) -> MasteryTargetSource:
    return MasteryTargetSource(
        family="CONCEPT",
        target_id=target_id,
        canonical_key=key,
        display_name=name,
        category=category,
        parent_concept_id=parent_id,
        parent_canonical_key=parent_key,
        parent_display_name=parent_name,
        facts=MasteryTargetFacts("CONCEPT", "NEW_GRAD", evidence, breakpoints),
    )


def _skill(
    target_id: UUID,
    key: str,
    name: str,
    evidence: tuple[MasteryEvidenceFact, ...],
) -> MasteryTargetSource:
    return MasteryTargetSource(
        family="SKILL",
        target_id=target_id,
        canonical_key=key,
        display_name=name,
        category="INTERVIEW_SKILL",
        parent_concept_id=None,
        parent_canonical_key=None,
        parent_display_name=None,
        facts=MasteryTargetFacts("SKILL", "NEW_GRAD", evidence),
    )


def _fact(
    number: int,
    *,
    session: int,
    problem: int,
    days_ago: int,
    polarity: EvidencePolarity,
    strength: EvidenceStrength,
    independence: EvidenceIndependence,
    context: str,
    family: str,
    problem_title: str,
    finding: str,
    mode: InterviewMode = "SIMULATION",
) -> MasteryEvidenceFact:
    return MasteryEvidenceFact(
        evidence_id=UUID(f"8a000000-0000-4000-8000-{number:012d}"),
        session_id=UUID(f"8a000000-0000-4000-8100-{session:012d}"),
        problem_version_id=UUID(f"8a000000-0000-4000-8200-{problem:012d}"),
        occurred_at=DEMO_NOW - timedelta(days=days_ago),
        polarity=polarity,
        strength=strength,
        independence=independence,
        evidence_type="CORRECTNESS" if family != "recursion" else "DEPTH",
        interview_mode=mode,
        interview_level="NEW_GRAD",
        context_key=f"mastery-context.v1:{context}",
        observation_key=f"mastery-observation.v1:{number}",
        concept_family_key=family,
        problem_title=problem_title,
        finding=finding,
    )

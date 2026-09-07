from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.evidence.models import Evidence
from app.mastery.development_fixtures import (
    DEMO_NOW,
    DevelopmentMasteryFixture,
    load_development_mastery_fixtures,
)
from app.mastery.models import MasteryTransition
from app.mastery.policy import (
    MASTERY_POLICY_VERSION,
    MasteryBreakpointFact,
    MasteryEvidenceFact,
    MasteryPolicyV1,
    MasteryProjectionDecision,
    MasteryTargetFacts,
)
from app.mastery.schema import CandidateMasteryOverviewResponse, CandidateMasteryTarget
from app.mastery.source import MasterySourceBuilder, _identity
from app.mastery.view import (
    PersistedMasteryProjection,
    _next_action,
    build_candidate_mastery_overview,
    build_persisted_candidate_mastery_overview,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MasteryIntegrityCase:
    number: int
    name: str
    check: Callable[[], None]


def _uuid(namespace: int, number: int) -> UUID:
    return UUID(f"8b000000-0000-4000-{namespace:04d}-{number:012d}")


def _fact(
    number: int,
    *,
    session: int = 1,
    problem: int = 1,
    stable_problem: int | None = None,
    days_ago: int = 1,
    polarity: str = "POSITIVE",
    strength: str = "STRONG",
    independence: str = "INDEPENDENT",
    context: str | None = None,
    observation: str | None = None,
    family: str = "sliding_window",
    level: str = "NEW_GRAD",
    valid: bool = True,
    is_retest: bool = False,
    is_self_correction: bool = False,
    application: bool = True,
    demonstration_form: str = "EXPLANATION",
) -> MasteryEvidenceFact:
    return MasteryEvidenceFact(
        evidence_id=_uuid(8000, number),
        session_id=_uuid(8100, session),
        problem_version_id=_uuid(8200, problem),
        problem_id=_uuid(8250, stable_problem if stable_problem is not None else problem),
        occurred_at=NOW - timedelta(days=days_ago),
        polarity=polarity,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        independence=independence,  # type: ignore[arg-type]
        evidence_type="CORRECTNESS",
        interview_mode="COACH" if independence == "DIRECTLY_TAUGHT" else "SIMULATION",
        interview_level=level,  # type: ignore[arg-type]
        context_key=context or f"context-{number}",
        observation_key=observation or f"observation-{number}",
        concept_family_key=family,
        demonstration_form=demonstration_form,
        valid=valid,
        is_retest=is_retest,
        is_self_correction=is_self_correction,
        demonstrates_reasoning_or_application=application,
    )


def _decision(
    *facts: MasteryEvidenceFact,
    family: str = "CONCEPT",
    level: str = "NEW_GRAD",
    breakpoints: tuple[MasteryBreakpointFact, ...] = (),
    now: datetime = NOW,
) -> MasteryProjectionDecision:
    return MasteryPolicyV1().evaluate(
        MasteryTargetFacts(
            family,  # type: ignore[arg-type]
            level,  # type: ignore[arg-type]
            facts,
            breakpoints,
        ),
        now=now,
    )


def _expect(
    decision: MasteryProjectionDecision,
    *,
    state: str,
    sufficiency: str | None = None,
    freshness: str | None = None,
    retest: bool | None = None,
) -> None:
    assert decision.state == state
    if sufficiency is not None:
        assert decision.evidence_sufficiency == sufficiency
    if freshness is not None:
        assert decision.freshness == freshness
    if retest is not None:
        assert decision.retest_due is retest


def _fixture(fixture_id: str) -> DevelopmentMasteryFixture:
    return next(
        item for item in load_development_mastery_fixtures() if item.fixture_id == fixture_id
    )


def _overview(fixture_id: str) -> CandidateMasteryOverviewResponse:
    return build_candidate_mastery_overview(_fixture(fixture_id).bundle, now=DEMO_NOW)


def _assert_equal(left: object, right: object) -> None:
    assert left == right


def _assert_not_equal(left: object, right: object) -> None:
    assert left != right


def _assert_true(value: object) -> None:
    assert value


def _assert_false(value: object) -> None:
    assert not value


def _assert_raises_invalid_clock(fact: MasteryEvidenceFact) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _decision(fact, now=datetime(2026, 9, 6, 12, 0))
    assert fact.valid is True


def _assert_parent_example() -> None:
    overview = _overview("multi-context-strong")
    parent = overview.parent_summaries[0]
    children = {item.state for item in overview.technical_concepts}
    assert parent.state == "DEVELOPING"
    assert "WEAK" in children and "STRONG" in children


def _assert_parent_has_no_synthetic_evidence() -> None:
    parent = _overview("multi-context-strong").parent_summaries[0]
    assert parent.evidence == []
    assert parent.child_target_ids


def _single_child_parent(child_state: str) -> CandidateMasteryTarget:
    fixture = _fixture("multi-context-strong")
    source = next(
        item
        for item in fixture.bundle.targets
        if item.family == "CONCEPT"
        and MasteryPolicyV1().evaluate(item.facts, now=DEMO_NOW).state == child_state
    )
    bundle = replace(fixture.bundle, targets=(source,))
    return build_candidate_mastery_overview(bundle, now=DEMO_NOW).parent_summaries[0]


def _parent_with_freshness(days_ago: int) -> CandidateMasteryTarget:
    fixture = _fixture("multi-context-strong")
    source = next(
        item
        for item in fixture.bundle.targets
        if item.family == "CONCEPT"
        and MasteryPolicyV1().evaluate(item.facts, now=DEMO_NOW).state == "STRONG"
    )
    facts = replace(
        source.facts,
        evidence=tuple(
            replace(item, occurred_at=DEMO_NOW - timedelta(days=days_ago))
            for item in source.facts.evidence
        ),
    )
    bundle = replace(fixture.bundle, targets=(replace(source, facts=facts),))
    return build_candidate_mastery_overview(bundle, now=DEMO_NOW).parent_summaries[0]


def _all_low_parent() -> CandidateMasteryTarget:
    fixture = _fixture("multi-context-strong")
    source = next(
        item
        for item in fixture.bundle.targets
        if item.canonical_key == "sliding_window_state_maintenance"
    )
    sibling = replace(
        source,
        target_id=_uuid(8700, 1),
        canonical_key="another_low_child",
        display_name="Another low child",
        facts=replace(
            source.facts,
            evidence=tuple(
                replace(
                    item,
                    evidence_id=_uuid(8701, index + 1),
                    observation_key=f"low-observation-{index}",
                )
                for index, item in enumerate(source.facts.evidence)
            ),
        ),
    )
    bundle = replace(fixture.bundle, targets=(source, sibling))
    return build_candidate_mastery_overview(bundle, now=DEMO_NOW).parent_summaries[0]


def _persisted_overview(
    *,
    state: str,
    policy_version: str = MASTERY_POLICY_VERSION,
    status: str | None = None,
) -> CandidateMasteryOverviewResponse:
    fixture = _fixture("multi-context-strong")
    source = next(
        item
        for item in fixture.bundle.targets
        if item.family == "CONCEPT"
        and MasteryPolicyV1().evaluate(item.facts, now=DEMO_NOW).state == "STRONG"
    )
    expected = MasteryPolicyV1().evaluate(source.facts, now=DEMO_NOW)
    projection = PersistedMasteryProjection(
        family="CONCEPT",
        target_id=source.target_id,
        state=state,  # type: ignore[arg-type]
        mastery_policy_version=policy_version,
        projection_version=7,
        last_evaluated_at=DEMO_NOW - timedelta(minutes=5),
        last_evidence_at=expected.last_evidence_at,
        supporting_evidence_count=len(expected.supporting_evidence_ids),
        context_diversity=expected.distinct_context_count,
        updated_at=DEMO_NOW - timedelta(minutes=5),
        contributions=tuple(
            (item.evidence_id, item.classification, item.context_key)
            for item in expected.contributions
        ),
    )
    bundle = replace(fixture.bundle, targets=(source,))
    return build_persisted_candidate_mastery_overview(
        bundle,
        (projection,),
        now=DEMO_NOW,
        status=status,
    )


def _persisted_untested_overview() -> CandidateMasteryOverviewResponse:
    fixture = _fixture("one-independent-session")
    source = fixture.bundle.targets[0]
    empty_source = replace(source, facts=replace(source.facts, evidence=()))
    projection = PersistedMasteryProjection(
        family="CONCEPT",
        target_id=source.target_id,
        state="UNTESTED",
        mastery_policy_version=MASTERY_POLICY_VERSION,
        projection_version=2,
        last_evaluated_at=DEMO_NOW,
        last_evidence_at=None,
        supporting_evidence_count=0,
        context_diversity=0,
        updated_at=DEMO_NOW,
        contributions=(),
    )
    return build_persisted_candidate_mastery_overview(
        replace(fixture.bundle, targets=(empty_source,)),
        (projection,),
        now=DEMO_NOW,
    )


def _skill_only_retest_overview() -> CandidateMasteryOverviewResponse:
    fixture = _fixture("strong-but-stale")
    source = fixture.bundle.targets[0]
    skill_source = replace(
        source,
        family="SKILL",
        target_id=_uuid(8900, 1),
        canonical_key="complexity_reasoning",
        display_name="Complexity reasoning",
        category="INTERVIEW_SKILL",
        parent_concept_id=None,
        parent_canonical_key=None,
        parent_display_name=None,
        facts=replace(source.facts, target_family="SKILL"),
    )
    return build_candidate_mastery_overview(
        replace(fixture.bundle, targets=(skill_source,)),
        now=DEMO_NOW,
    )


def _concept_retest_overview() -> CandidateMasteryOverviewResponse:
    fixture = _fixture("strong-but-stale")
    return build_candidate_mastery_overview(fixture.bundle, now=DEMO_NOW)


def _weak_without_retest_overview() -> CandidateMasteryOverviewResponse:
    return _overview("multi-context-strong")


def _weak_without_retest_target() -> CandidateMasteryTarget:
    return next(
        item
        for item in _weak_without_retest_overview().technical_concepts
        if item.state == "WEAK"
    )


def _assert_candidate_contract_has_no_percentage() -> None:
    payload = _overview("multi-context-strong").model_dump(mode="json")
    rendered = repr(payload).lower()
    assert "percentage" not in rendered
    assert "percent" not in rendered
    assert "score" not in rendered


def _assert_sparse_untested_overview() -> None:
    overview = _overview("cold-start")
    assert overview.status == "EMPTY"
    assert overview.technical_concepts == []
    assert overview.interview_skills == []


def _assert_mastery_is_not_evidence() -> None:
    evidence_columns = set(Evidence.__table__.columns.keys())
    assert "mastery_state" not in evidence_columns
    assert MasteryTransition.__tablename__ != Evidence.__tablename__


def _assert_source_excludes_derived_prose() -> None:
    module = inspect.getmodule(MasterySourceBuilder)
    assert module is not None
    source = inspect.getsource(module)
    assert "SessionReport" not in source
    assert "CounterMap" not in source
    assert ".reports" not in source
    assert ".countermap" not in source


def _assert_no_ai_dependency() -> None:
    modules = (
        inspect.getmodule(MasteryPolicyV1),
        inspect.getmodule(MasterySourceBuilder),
    )
    source = "\n".join(inspect.getsource(module) for module in modules if module is not None)
    assert "AIGateway" not in source
    assert "ReasoningProvider" not in source
    assert "OpenAI" not in source


def _assert_contributions(
    decision: MasteryProjectionDecision,
    expected: tuple[str, ...],
) -> None:
    assert tuple(item.classification for item in decision.contributions) == expected


def _cases() -> list[MasteryIntegrityCase]:
    strong_one = _fact(1)
    strong_two = _fact(2, session=2, problem=2, context="different-context")
    same_context = _fact(3, session=2, problem=2, context=strong_one.context_key)
    rich_same_session = (
        _fact(4, context="rich-a"),
        _fact(5, context="rich-b"),
        _fact(6, context="rich-c"),
    )
    strong_negative = _fact(7, polarity="NEGATIVE")
    moderate_negative = _fact(8, polarity="NEGATIVE", strength="MODERATE")
    second_moderate_negative = _fact(
        9,
        session=2,
        problem=2,
        polarity="NEGATIVE",
        strength="MODERATE",
    )
    hinted_negative = _fact(10, polarity="NEGATIVE", independence="AFTER_STRONG_HINT")
    taught = _fact(11, independence="DIRECTLY_TAUGHT")
    after_probe = _fact(12, independence="AFTER_PROBE")
    corrected = _fact(13, days_ago=0, is_self_correction=True)
    stale_one = replace(strong_one, occurred_at=NOW - timedelta(days=240))
    stale_two = replace(strong_two, occurred_at=NOW - timedelta(days=200))
    aging_one = replace(strong_one, occurred_at=NOW - timedelta(days=120))
    aging_two = replace(strong_two, occurred_at=NOW - timedelta(days=100))
    breakpoint = MasteryBreakpointFact(
        _uuid(8300, 1),
        "OPEN",
        "HIGH",
        NOW - timedelta(days=1),
        frozenset((strong_negative.evidence_id,)),
    )
    skill_two = replace(strong_two, concept_family_key="hashing")
    memorized = replace(
        strong_two,
        context_key=strong_one.context_key,
        demonstrates_reasoning_or_application=False,
    )
    three_meaningful = (*rich_same_session,)
    lower_priority_breakpoint = MasteryBreakpointFact(
        _uuid(8300, 2),
        "OPEN",
        "LOW",
        NOW - timedelta(days=30),
        frozenset((strong_negative.evidence_id,)),
    )
    higher_priority_breakpoint = MasteryBreakpointFact(
        _uuid(8300, 3),
        "OPEN",
        "HIGH",
        NOW - timedelta(days=1),
        frozenset((strong_negative.evidence_id,)),
    )

    cases = [
        MasteryIntegrityCase(
            1, "no evidence is untested", lambda: _expect(_decision(), state="UNTESTED")
        ),
        MasteryIntegrityCase(
            2,
            "shallow evidence is exposed",
            lambda: _expect(_decision(_fact(20, strength="WEAK")), state="EXPOSED"),
        ),
        MasteryIntegrityCase(
            3,
            "one independent answer develops",
            lambda: _expect(_decision(strong_one), state="DEVELOPING"),
        ),
        MasteryIntegrityCase(
            4,
            "one rich session is not strong",
            lambda: _expect(_decision(*rich_same_session), state="DEVELOPING", sufficiency="HIGH"),
        ),
        MasteryIntegrityCase(
            5,
            "distinct contexts earn strong",
            lambda: _expect(_decision(strong_one, strong_two), state="STRONG"),
        ),
        MasteryIntegrityCase(
            6,
            "exact context repetition does not inflate",
            lambda: _expect(_decision(strong_one, memorized), state="DEVELOPING"),
        ),
        MasteryIntegrityCase(
            7,
            "diagnostic negative can be weak",
            lambda: _expect(_decision(strong_negative), state="WEAK"),
        ),
        MasteryIntegrityCase(
            8,
            "ordinary error is not automatically weak",
            lambda: _expect(_decision(moderate_negative), state="EXPOSED"),
        ),
        MasteryIntegrityCase(
            9,
            "aligned moderate negatives can be weak",
            lambda: _expect(_decision(moderate_negative, second_moderate_negative), state="WEAK"),
        ),
        MasteryIntegrityCase(
            10,
            "strong hint does not prove weak",
            lambda: _expect(_decision(hinted_negative), state="EXPOSED"),
        ),
        MasteryIntegrityCase(
            11,
            "teaching cannot create strong",
            lambda: _expect(_decision(taught), state="EXPOSED", retest=True),
        ),
        MasteryIntegrityCase(
            12,
            "pre-help gap survives teaching",
            lambda: _expect(
                _decision(strong_negative, taught), state="WEAK", freshness="RETEST_DUE"
            ),
        ),
        MasteryIntegrityCase(
            13,
            "after-probe evidence is meaningful",
            lambda: _expect(_decision(after_probe), state="DEVELOPING"),
        ),
        MasteryIntegrityCase(
            14,
            "self-correction avoids false weak",
            lambda: _expect(_decision(moderate_negative, corrected), state="DEVELOPING"),
        ),
        MasteryIntegrityCase(
            15,
            "contradiction develops",
            lambda: _expect(
                _decision(strong_one, strong_negative), state="DEVELOPING", retest=True
            ),
        ),
        MasteryIntegrityCase(
            16,
            "stale strong stays strong",
            lambda: _expect(
                _decision(stale_one, stale_two), state="STRONG", freshness="RETEST_DUE"
            ),
        ),
        MasteryIntegrityCase(
            17,
            "time never creates weak",
            lambda: _assert_not_equal(_decision(stale_one).state, "WEAK"),
        ),
        MasteryIntegrityCase(
            18,
            "breakpoint influences retest",
            lambda: _expect(
                _decision(strong_one, breakpoints=(breakpoint,)), state="DEVELOPING", retest=True
            ),
        ),
        MasteryIntegrityCase(
            19,
            "assistance does not resolve breakpoint",
            lambda: _expect(
                _decision(taught, breakpoints=(breakpoint,)),
                state="EXPOSED",
                freshness="RETEST_DUE",
            ),
        ),
        MasteryIntegrityCase(
            20,
            "one debugging correction is not skill strong",
            lambda: _expect(
                _decision(replace(strong_one, evidence_type="DEBUGGING"), family="SKILL"),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            21,
            "skill strong needs multiple contexts",
            lambda: _expect(_decision(strong_one, skill_two, family="SKILL"), state="STRONG"),
        ),
        MasteryIntegrityCase(
            22,
            "complexity across mixed concepts develops",
            lambda: _expect(
                _decision(
                    strong_one,
                    replace(strong_negative, concept_family_key="hashing"),
                    family="SKILL",
                ),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            23,
            "evidence can support concept and skill",
            lambda: _assert_equal(
                (_decision(strong_one).state, _decision(strong_one, family="SKILL").state),
                ("DEVELOPING", "DEVELOPING"),
            ),
        ),
        MasteryIntegrityCase(24, "parent example is conservative", _assert_parent_example),
        MasteryIntegrityCase(
            25, "parent emits no synthetic evidence", _assert_parent_has_no_synthetic_evidence
        ),
        MasteryIntegrityCase(
            26,
            "invalidated evidence is excluded",
            lambda: _expect(_decision(replace(strong_one, valid=False)), state="UNTESTED"),
        ),
        MasteryIntegrityCase(
            27,
            "invalidation can lower strong",
            lambda: _expect(
                _decision(strong_one, replace(strong_two, valid=False)), state="DEVELOPING"
            ),
        ),
        MasteryIntegrityCase(
            28,
            "all invalidated returns untested",
            lambda: _expect(
                _decision(replace(strong_one, valid=False), replace(strong_two, valid=False)),
                state="UNTESTED",
            ),
        ),
        MasteryIntegrityCase(
            29,
            "removed session leaves no ghost fact",
            lambda: _expect(_decision(), state="UNTESTED", sufficiency="LOW"),
        ),
        MasteryIntegrityCase(
            30,
            "policy version does not rewrite evidence",
            lambda: _assert_equal(
                (strong_one, MasteryPolicyV1().version), (strong_one, MASTERY_POLICY_VERSION)
            ),
        ),
        MasteryIntegrityCase(
            31,
            "duplicate evaluation is idempotent",
            lambda: _assert_equal(
                _decision(strong_one, strong_two), _decision(strong_one, strong_two)
            ),
        ),
        MasteryIntegrityCase(
            32,
            "repeated jobs converge",
            lambda: _assert_equal(
                _decision(*reversed((strong_one, strong_two))), _decision(strong_one, strong_two)
            ),
        ),
        MasteryIntegrityCase(
            33,
            "association admissions are exact",
            lambda: _assert_contributions(
                _decision(strong_one, strong_negative, taught),
                ("SUPPORTING", "CONTRADICTING", "LEARNING_LIMITED"),
            ),
        ),
        MasteryIntegrityCase(
            34,
            "transition evidence identity is exact",
            lambda: _assert_equal(
                set(_decision(strong_one, strong_two).supporting_evidence_ids),
                {strong_one.evidence_id, strong_two.evidence_id},
            ),
        ),
        MasteryIntegrityCase(
            35,
            "duplicate input has stable transition basis",
            lambda: _assert_equal(
                tuple(item.evidence_id for item in _decision(strong_one).contributions),
                (strong_one.evidence_id,),
            ),
        ),
        MasteryIntegrityCase(
            36,
            "first meaningful state is auditable",
            lambda: _assert_true(
                "from_state" in MasteryTransition.__table__.columns
                and "to_state" in MasteryTransition.__table__.columns
            ),
        ),
        MasteryIntegrityCase(
            37,
            "context identity is deterministic",
            lambda: _assert_equal(
                _identity("context", "p", "SIMULATION"), _identity("context", "p", "SIMULATION")
            ),
        ),
        MasteryIntegrityCase(
            38,
            "identical questions do not fake diversity",
            lambda: _assert_equal(_decision(strong_one, same_context).distinct_context_count, 1),
        ),
        MasteryIntegrityCase(
            39,
            "different contexts count separately",
            lambda: _assert_equal(_decision(strong_one, strong_two).distinct_context_count, 2),
        ),
        MasteryIntegrityCase(
            40,
            "low sufficiency is deterministic",
            lambda: _expect(
                _decision(_fact(40, strength="WEAK")), state="EXPOSED", sufficiency="LOW"
            ),
        ),
        MasteryIntegrityCase(
            41,
            "medium sufficiency is deterministic",
            lambda: _expect(_decision(strong_one), state="DEVELOPING", sufficiency="MEDIUM"),
        ),
        MasteryIntegrityCase(
            42,
            "high sufficiency is deterministic",
            lambda: _expect(_decision(*three_meaningful), state="DEVELOPING", sufficiency="HIGH"),
        ),
        MasteryIntegrityCase(
            43,
            "current freshness is deterministic",
            lambda: _expect(_decision(strong_one), state="DEVELOPING", freshness="CURRENT"),
        ),
        MasteryIntegrityCase(
            44,
            "aging freshness is deterministic",
            lambda: _expect(_decision(aging_one, aging_two), state="STRONG", freshness="AGING"),
        ),
        MasteryIntegrityCase(
            45,
            "retest-due freshness is deterministic",
            lambda: _expect(
                _decision(stale_one, stale_two), state="STRONG", freshness="RETEST_DUE"
            ),
        ),
        MasteryIntegrityCase(
            46,
            "unverified teaching recommends retest",
            lambda: _assert_equal(_decision(taught).retest_reason, "INDEPENDENCE_NOT_VERIFIED"),
        ),
        MasteryIntegrityCase(
            47,
            "stale strong recommends retest",
            lambda: _assert_equal(
                _decision(stale_one, stale_two).retest_reason, "STALE_VERIFICATION"
            ),
        ),
        MasteryIntegrityCase(
            48,
            "contradiction recommends retest",
            lambda: _assert_equal(
                _decision(strong_one, strong_negative).retest_reason, "CONTRADICTORY_EVIDENCE"
            ),
        ),
        MasteryIntegrityCase(
            49,
            "clean strong needs no retest",
            lambda: _assert_false(_decision(strong_one, strong_two).retest_due),
        ),
        MasteryIntegrityCase(
            50, "untested needs no retest", lambda: _assert_false(_decision().retest_due)
        ),
        MasteryIntegrityCase(
            51, "candidate API has no percentage", _assert_candidate_contract_has_no_percentage
        ),
        MasteryIntegrityCase(
            52, "untested ontology stays sparse", _assert_sparse_untested_overview
        ),
        MasteryIntegrityCase(
            53,
            "lower-level history does not become weak",
            lambda: _expect(
                _decision(
                    replace(strong_one, interview_level="INTERN"),
                    replace(strong_two, interview_level="INTERN"),
                    level="EARLY_CAREER",
                ),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            54, "derived prose is not an input", _assert_source_excludes_derived_prose
        ),
        MasteryIntegrityCase(55, "mastery never becomes evidence", _assert_mastery_is_not_evidence),
        MasteryIntegrityCase(
            56,
            "failure preserves canonical facts",
            lambda: _assert_raises_invalid_clock(strong_one),
        ),
        MasteryIntegrityCase(
            57,
            "outbox identity is deterministic",
            lambda: _assert_equal(
                _identity("mastery-job", "user", "session"),
                _identity("mastery-job", "user", "session"),
            ),
        ),
        MasteryIntegrityCase(58, "mastery generation requires no AI", _assert_no_ai_dependency),
        MasteryIntegrityCase(
            59,
            "execution-only strong negative is not weak",
            lambda: _expect(
                _decision(replace(strong_negative, demonstrates_reasoning_or_application=False)),
                state="EXPOSED",
            ),
        ),
        MasteryIntegrityCase(
            60,
            "defended strong negative remains diagnostic",
            lambda: _expect(_decision(strong_negative), state="WEAK"),
        ),
        MasteryIntegrityCase(
            61,
            "mixed independent correction develops",
            lambda: _expect(
                _decision(_fact(61, polarity="MIXED", is_self_correction=True)),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            62,
            "mixed correction remains supporting without polarity rewrite",
            lambda: _assert_equal(
                (
                    _decision(
                        _fact(62, polarity="MIXED", is_self_correction=True)
                    ).contributions[0].classification,
                    _fact(62, polarity="MIXED", is_self_correction=True).polarity,
                ),
                ("SUPPORTING", "MIXED"),
            ),
        ),
        MasteryIntegrityCase(
            63,
            "after-probe mixed correction is not independent",
            lambda: _expect(
                _decision(
                    _fact(
                        63,
                        polarity="MIXED",
                        independence="AFTER_PROBE",
                        is_self_correction=True,
                    )
                ),
                state="EXPOSED",
            ),
        ),
        MasteryIntegrityCase(
            64,
            "assisted mixed correction is not independent",
            lambda: _expect(
                _decision(
                    _fact(
                        64,
                        polarity="MIXED",
                        independence="AFTER_LIGHT_GUIDANCE",
                        is_self_correction=True,
                    )
                ),
                state="EXPOSED",
            ),
        ),
        MasteryIntegrityCase(
            65,
            "self-correction alone cannot become strong",
            lambda: _assert_not_equal(
                _decision(_fact(65, polarity="MIXED", is_self_correction=True)).state,
                "STRONG",
            ),
        ),
        MasteryIntegrityCase(
            66,
            "isolated negative plus mixed correction develops",
            lambda: _expect(
                _decision(
                    moderate_negative,
                    _fact(66, days_ago=0, polarity="MIXED", is_self_correction=True),
                ),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            67,
            "repeated misconception can remain weak after correction",
            lambda: _expect(
                _decision(
                    moderate_negative,
                    second_moderate_negative,
                    _fact(67, days_ago=0, polarity="MIXED", is_self_correction=True),
                ),
                state="WEAK",
            ),
        ),
        MasteryIntegrityCase(
            68,
            "stable problem count ignores immutable version count",
            lambda: _assert_equal(
                _decision(
                    _fact(68, problem=1, stable_problem=1),
                    _fact(
                        69,
                        session=2,
                        problem=2,
                        stable_problem=1,
                        context="grounded-second-form",
                    ),
                ).distinct_problem_count,
                1,
            ),
        ),
        MasteryIntegrityCase(
            69,
            "skill strong needs distinct stable problems",
            lambda: _expect(
                _decision(
                    _fact(70, problem=1, stable_problem=1),
                    _fact(
                        71,
                        session=2,
                        problem=2,
                        stable_problem=1,
                        context="different-context",
                        family="hashing",
                    ),
                    family="SKILL",
                ),
                state="DEVELOPING",
            ),
        ),
        MasteryIntegrityCase(
            70,
            "duplicate observation rows do not create high sufficiency",
            lambda: _expect(
                _decision(
                    _fact(72, observation="same-observation", context="same-context"),
                    _fact(73, observation="same-observation", context="same-context"),
                    _fact(74, observation="same-observation", context="same-context"),
                ),
                state="DEVELOPING",
                sufficiency="MEDIUM",
            ),
        ),
        MasteryIntegrityCase(
            71,
            "rich distinct forms in one session may be high",
            lambda: _expect(
                _decision(
                    _fact(75, context="explanation", demonstration_form="EXPLANATION"),
                    _fact(76, context="implementation", demonstration_form="IMPLEMENTATION"),
                    _fact(77, context="transfer", demonstration_form="TRANSFER"),
                ),
                state="DEVELOPING",
                sufficiency="HIGH",
            ),
        ),
        MasteryIntegrityCase(
            72,
            "after-probe explanation preserves diagnostic attribution",
            lambda: _assert_true("diagnostic challenge" in _decision(after_probe).explanation),
        ),
        MasteryIntegrityCase(
            73,
            "after-probe weak explanation does not claim independence",
            lambda: _assert_true(
                "Diagnostic evidence"
                in _decision(
                    replace(strong_negative, independence="AFTER_PROBE")
                ).explanation
            ),
        ),
        MasteryIntegrityCase(
            74,
            "one strong child does not generalize parent strong",
            lambda: _assert_equal(_single_child_parent("STRONG").state, "DEVELOPING"),
        ),
        MasteryIntegrityCase(
            75,
            "one weak child does not generalize parent weak",
            lambda: _assert_equal(_single_child_parent("WEAK").state, "EXPOSED"),
        ),
        MasteryIntegrityCase(
            76,
            "low child coverage does not make parent high",
            lambda: _assert_equal(_all_low_parent().evidence_sufficiency, "LOW"),
        ),
        MasteryIntegrityCase(
            77,
            "aging child keeps parent aging",
            lambda: _assert_equal(_parent_with_freshness(100).freshness, "AGING"),
        ),
        MasteryIntegrityCase(
            78,
            "retest-due child keeps parent retest due",
            lambda: _assert_equal(_parent_with_freshness(200).freshness, "RETEST_DUE"),
        ),
        MasteryIntegrityCase(
            79,
            "persisted state wins over shadow policy result",
            lambda: _assert_equal(
                (
                    _persisted_overview(state="DEVELOPING").technical_concepts[0].state,
                    _persisted_overview(state="DEVELOPING").status,
                ),
                ("DEVELOPING", "STALE"),
            ),
        ),
        MasteryIntegrityCase(
            80,
            "unknown persisted policy is never relabeled as v1",
            lambda: _assert_equal(
                (
                    _persisted_overview(
                        state="WEAK", policy_version="mastery_policy_v2"
                    ).technical_concepts[0].state,
                    _persisted_overview(
                        state="WEAK", policy_version="mastery_policy_v2"
                    ).mastery_policy_version,
                    _persisted_overview(
                        state="WEAK", policy_version="mastery_policy_v2"
                    ).status,
                ),
                ("WEAK", "mastery_policy_v2", "STALE"),
            ),
        ),
        MasteryIntegrityCase(
            81,
            "execution-only mixed rows do not create high sufficiency",
            lambda: _expect(
                _decision(
                    *(
                        _fact(
                            80 + index,
                            polarity="MIXED",
                            application=False,
                            demonstration_form="EXECUTION_ONLY",
                        )
                        for index in range(1, 4)
                    )
                ),
                state="EXPOSED",
                sufficiency="LOW",
            ),
        ),
        MasteryIntegrityCase(
            82,
            "context variants of one mixed observation do not create high sufficiency",
            lambda: _expect(
                _decision(
                    *(
                        _fact(
                            83 + index,
                            polarity="MIXED",
                            observation="shared-candidate-observation",
                            context=f"context-only-variant-{index}",
                        )
                        for index in range(1, 4)
                    )
                ),
                state="EXPOSED",
                sufficiency="MEDIUM",
            ),
        ),
        MasteryIntegrityCase(
            83,
            "later independent mixed self-correction verifies teaching",
            lambda: _expect(
                _decision(
                    taught,
                    _fact(
                        87,
                        days_ago=0,
                        polarity="MIXED",
                        is_self_correction=True,
                    ),
                ),
                state="DEVELOPING",
                freshness="CURRENT",
                retest=False,
            ),
        ),
        MasteryIntegrityCase(
            84,
            "after-probe mixed correction does not verify teaching independently",
            lambda: _assert_equal(
                _decision(
                    taught,
                    _fact(
                        88,
                        days_ago=0,
                        polarity="MIXED",
                        independence="AFTER_PROBE",
                        is_self_correction=True,
                    ),
                ).retest_reason,
                "INDEPENDENCE_NOT_VERIFIED",
            ),
        ),
        MasteryIntegrityCase(
            85,
            "assisted mixed correction does not verify teaching independently",
            lambda: _assert_equal(
                _decision(
                    taught,
                    _fact(
                        89,
                        days_ago=0,
                        polarity="MIXED",
                        independence="AFTER_LIGHT_GUIDANCE",
                        is_self_correction=True,
                    ),
                ).retest_reason,
                "INDEPENDENCE_NOT_VERIFIED",
            ),
        ),
        MasteryIntegrityCase(
            86,
            "verified mixed self-correction remains mixed",
            lambda: _assert_equal(
                _fact(90, polarity="MIXED", is_self_correction=True).polarity,
                "MIXED",
            ),
        ),
        MasteryIntegrityCase(
            87,
            "all-independent strong copy states repeated independence",
            lambda: _assert_equal(
                _decision(strong_one, strong_two).explanation,
                "You demonstrated this independently across multiple distinct contexts.",
            ),
        ),
        MasteryIntegrityCase(
            88,
            "after-probe strong copy preserves mixed independence",
            lambda: _assert_equal(
                _decision(
                    strong_one,
                    replace(strong_two, independence="AFTER_PROBE"),
                ).explanation,
                "You demonstrated this across multiple distinct contexts, including fully "
                "independent evidence.",
            ),
        ),
        MasteryIntegrityCase(
            89,
            "breakpoint priority ignores input order",
            lambda: _assert_equal(
                (
                    _decision(
                        strong_one,
                        breakpoints=(
                            lower_priority_breakpoint,
                            higher_priority_breakpoint,
                        ),
                    ).unresolved_breakpoint_ids,
                    _decision(
                        strong_one,
                        breakpoints=(
                            higher_priority_breakpoint,
                            lower_priority_breakpoint,
                        ),
                    ).unresolved_breakpoint_ids,
                ),
                (
                    (
                        higher_priority_breakpoint.breakpoint_id,
                        lower_priority_breakpoint.breakpoint_id,
                    ),
                    (
                        higher_priority_breakpoint.breakpoint_id,
                        lower_priority_breakpoint.breakpoint_id,
                    ),
                ),
            ),
        ),
        MasteryIntegrityCase(
            90,
            "skill-only fixture retest is not an actionable recommendation",
            lambda: _assert_equal(
                (
                    _skill_only_retest_overview().interview_skills[0].retest_due,
                    _skill_only_retest_overview().interview_skills[0].recommendation_id,
                    _skill_only_retest_overview().retest_recommendations,
                ),
                (True, None, []),
            ),
        ),
        MasteryIntegrityCase(
            91,
            "concept fixture retest keeps an actionable recommendation",
            lambda: _assert_true(_concept_retest_overview().retest_recommendations),
        ),
        MasteryIntegrityCase(
            92,
            "all-untested persisted projection is cold start",
            lambda: _assert_equal(
                (
                    _persisted_untested_overview().status,
                    _persisted_untested_overview().technical_concepts,
                    _persisted_untested_overview().message,
                ),
                (
                    "EMPTY",
                    [],
                    "CounterQ is still learning where your interview strengths are. "
                    "Complete a few interviews to build an evidence-backed view.",
                ),
            ),
        ),
        MasteryIntegrityCase(
            93,
            "mixed verification can leave a separate contradiction retest",
            lambda: _assert_equal(
                _decision(
                    strong_negative,
                    taught,
                    _fact(
                        91,
                        days_ago=0,
                        polarity="MIXED",
                        is_self_correction=True,
                    ),
                ).retest_reason,
                "CONTRADICTORY_EVIDENCE",
            ),
        ),
        MasteryIntegrityCase(
            94,
            "mixed verification removes the false independence explanation",
            lambda: _assert_false(
                "not yet seen you verify this independently"
                in _decision(
                    taught,
                    _fact(
                        92,
                        days_ago=0,
                        polarity="MIXED",
                        is_self_correction=True,
                    ),
                ).explanation
            ),
        ),
        MasteryIntegrityCase(
            95,
            "recent independent mixed self-correction refreshes old verification",
            lambda: _expect(
                _decision(
                    stale_one,
                    taught,
                    _fact(
                        193,
                        days_ago=0,
                        polarity="MIXED",
                        is_self_correction=True,
                    ),
                ),
                state="DEVELOPING",
                freshness="CURRENT",
                retest=False,
            ),
        ),
        MasteryIntegrityCase(
            96,
            "recent after-probe mixed correction does not refresh taught verification",
            lambda: _assert_equal(
                _decision(
                    stale_one,
                    taught,
                    _fact(
                        194,
                        days_ago=0,
                        polarity="MIXED",
                        independence="AFTER_PROBE",
                        is_self_correction=True,
                    ),
                ).retest_reason,
                "INDEPENDENCE_NOT_VERIFIED",
            ),
        ),
        MasteryIntegrityCase(
            97,
            "recent guided mixed correction does not refresh taught verification",
            lambda: _assert_equal(
                _decision(
                    stale_one,
                    taught,
                    _fact(
                        195,
                        days_ago=0,
                        polarity="MIXED",
                        independence="AFTER_LIGHT_GUIDANCE",
                        is_self_correction=True,
                    ),
                ).retest_reason,
                "INDEPENDENCE_NOT_VERIFIED",
            ),
        ),
        MasteryIntegrityCase(
            98,
            "fresh mixed self-correction remains mixed and below strong",
            lambda: _assert_equal(
                (
                    _decision(
                        stale_one,
                        taught,
                        _fact(
                            196,
                            days_ago=0,
                            polarity="MIXED",
                            is_self_correction=True,
                        ),
                    ).state,
                    _fact(
                        196,
                        days_ago=0,
                        polarity="MIXED",
                        is_self_correction=True,
                    ).polarity,
                ),
                ("DEVELOPING", "MIXED"),
            ),
        ),
        MasteryIntegrityCase(
            99,
            "non-diagnostic context does not unlock high across meaningful sessions",
            lambda: _assert_equal(
                _decision(
                    _fact(
                        197,
                        session=20,
                        context="one-meaningful-context",
                        strength="MODERATE",
                    ),
                    _fact(
                        198,
                        session=21,
                        context="one-meaningful-context",
                        strength="MODERATE",
                    ),
                    _fact(
                        199,
                        session=22,
                        context="one-meaningful-context",
                        strength="MODERATE",
                    ),
                    _fact(
                        200,
                        session=23,
                        context="non-diagnostic-context",
                        application=False,
                    ),
                ).evidence_sufficiency,
                "MEDIUM",
            ),
        ),
        MasteryIntegrityCase(
            100,
            "non-diagnostic second session does not dilute rich single-session evidence",
            lambda: _assert_equal(
                _decision(
                    _fact(
                        201,
                        session=30,
                        context="rich-single-session",
                        strength="MODERATE",
                    ),
                    _fact(
                        202,
                        session=30,
                        context="rich-single-session",
                        strength="MODERATE",
                    ),
                    _fact(
                        203,
                        session=30,
                        context="rich-single-session",
                        strength="MODERATE",
                    ),
                    _fact(
                        204,
                        session=31,
                        context="rich-single-session",
                        application=False,
                    ),
                ).evidence_sufficiency,
                "HIGH",
            ),
        ),
        MasteryIntegrityCase(
            101,
            "genuine meaningful context diversity unlocks high sufficiency",
            lambda: _assert_equal(
                _decision(
                    _fact(
                        205,
                        session=40,
                        context="meaningful-context-a",
                        strength="MODERATE",
                    ),
                    _fact(
                        206,
                        session=41,
                        context="meaningful-context-b",
                        strength="MODERATE",
                    ),
                    _fact(
                        207,
                        session=42,
                        context="meaningful-context-b",
                        strength="MODERATE",
                    ),
                ).evidence_sufficiency,
                "HIGH",
            ),
        ),
        MasteryIntegrityCase(
            102,
            "duplicate meaningful observations remain one diagnostic unit",
            lambda: _assert_equal(
                _decision(
                    _fact(
                        208,
                        session=50,
                        context="duplicate-context-a",
                        observation="duplicate-observation",
                    ),
                    _fact(
                        209,
                        session=51,
                        context="duplicate-context-b",
                        observation="duplicate-observation",
                    ),
                    _fact(
                        210,
                        session=52,
                        context="duplicate-context-c",
                        observation="duplicate-observation",
                    ),
                ).evidence_sufficiency,
                "MEDIUM",
            ),
        ),
        MasteryIntegrityCase(
            103,
            "weak next action does not deny meaningful evidence",
            lambda: _assert_false(
                "needs meaningful evidence" in _weak_without_retest_target().next_action.lower()
            ),
        ),
        MasteryIntegrityCase(
            104,
            "weak next action requests clean independent context",
            lambda: _assert_equal(
                _weak_without_retest_target().next_action,
                "Revisit this gap in a clean independent context and show the reasoning holds.",
            ),
        ),
        MasteryIntegrityCase(
            105,
            "weak unresolved breakpoint keeps its specific next action",
            lambda: _assert_equal(
                _next_action(
                    _decision(strong_negative, breakpoints=(breakpoint,))
                ),
                "Verify the unresolved gap independently in a different context.",
            ),
        ),
        MasteryIntegrityCase(
            106,
            "weak explicit independence retest keeps its specific next action",
            lambda: _assert_equal(
                _next_action(_decision(strong_negative, taught)),
                "Verify this independently without repeating the taught prompt.",
            ),
        ),
        MasteryIntegrityCase(
            107,
            "weak alone does not fabricate retest workflow",
            lambda: _assert_equal(
                (
                    _weak_without_retest_target().retest_due,
                    _weak_without_retest_target().recommendation_id,
                    _weak_without_retest_overview().retest_recommendations,
                ),
                (False, None, []),
            ),
        ),
        MasteryIntegrityCase(
            108,
            "mastery overview copy covers all evidence outcomes",
            lambda: _assert_equal(
                _weak_without_retest_overview().message,
                "What CounterQ has learned from your evidence across interviews.",
            ),
        ),
    ]
    assert [item.number for item in cases] == list(range(1, 109))
    return cases


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda item: f"{item.number:02d}-{item.name}",
)
def test_stage8_deterministic_mastery_corpus(case: MasteryIntegrityCase) -> None:
    case.check()


def test_stage8_evaluation_case_count() -> None:
    assert len(_cases()) == 108


def test_persisted_projection_metadata_is_read_truth() -> None:
    overview = _persisted_overview(state="STRONG")
    target = overview.technical_concepts[0]

    assert overview.status == "READY"
    assert target.state == "STRONG"
    assert target.mastery_policy_version == MASTERY_POLICY_VERSION
    assert target.projection_version == 7
    assert target.last_evidence_at is not None
    assert target.supporting_evidence_count == 2
    assert target.context_diversity == 2
    assert target.last_evaluated_at == DEMO_NOW - timedelta(minutes=5)
    assert target.projection_updated_at == DEMO_NOW - timedelta(minutes=5)


def test_persisted_inconsistency_preserves_failed_status_and_state() -> None:
    overview = _persisted_overview(state="DEVELOPING", status="FAILED")

    assert overview.status == "FAILED"
    assert overview.technical_concepts[0].state == "DEVELOPING"


def test_unpersisted_demo_fixture_still_evaluates_production_policy() -> None:
    overview = _overview("multi-context-strong")

    assert any(item.state == "STRONG" for item in overview.technical_concepts)
    assert all(item.mastery_policy_version is None for item in overview.technical_concepts)


def test_non_fixture_view_does_not_fabricate_retest_workflow_state() -> None:
    fixture = _fixture("strong-but-stale")
    overview = build_candidate_mastery_overview(
        fixture.bundle,
        now=DEMO_NOW,
        recommendation_ids={},
    )

    assert overview.technical_concepts[0].retest_due is True
    assert overview.technical_concepts[0].recommendation_id is None
    assert overview.retest_recommendations == []

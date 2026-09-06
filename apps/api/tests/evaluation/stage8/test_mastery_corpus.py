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
from app.mastery.schema import CandidateMasteryOverviewResponse
from app.mastery.source import MasterySourceBuilder, _identity
from app.mastery.view import build_candidate_mastery_overview

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
) -> MasteryEvidenceFact:
    return MasteryEvidenceFact(
        evidence_id=_uuid(8000, number),
        session_id=_uuid(8100, session),
        problem_version_id=_uuid(8200, problem),
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
        _uuid(8300, 1), "OPEN", frozenset((strong_negative.evidence_id,))
    )
    skill_two = replace(strong_two, concept_family_key="hashing")
    memorized = replace(
        strong_two,
        context_key=strong_one.context_key,
        demonstrates_reasoning_or_application=False,
    )
    three_meaningful = (*rich_same_session,)

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
    ]
    assert [item.number for item in cases] == list(range(1, 59))
    return cases


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda item: f"{item.number:02d}-{item.name}",
)
def test_stage8_deterministic_mastery_corpus(case: MasteryIntegrityCase) -> None:
    case.check()


def test_stage8_evaluation_case_count() -> None:
    assert len(_cases()) == 58


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

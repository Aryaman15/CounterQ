"""Bounded validation for strong-model Interview Pack and private-case output."""

from __future__ import annotations

import re
from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from app.ai_gateway.structured_output import StrictReasoningOutputModel
from app.problems.content import (
    CuratedContent,
    InterviewPackContent,
    ProblemContent,
    SemanticType,
    VisibleCase,
    validate_semantic_value,
)

BoundedString = Annotated[str, Field(max_length=4_096)]
BoundedSemanticText = Annotated[str, Field(min_length=1, max_length=4_096)]
BoundedSourceCode = Annotated[str, Field(min_length=1, max_length=100_000)]
BoundedConceptKey = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=160),
]
BoundedReferenceIndex = Annotated[int, Field(ge=0, le=31)]
BoundedIntArray = Annotated[list[int], Field(max_length=256)]
BoundedStringArray = Annotated[list[BoundedString], Field(max_length=256)]
BoundedIntMatrix = Annotated[list[BoundedIntArray], Field(max_length=64)]
BoundedStringMatrix = Annotated[list[BoundedStringArray], Field(max_length=64)]
PreparedCaseValue = (
    bool
    | int
    | BoundedString
    | BoundedIntArray
    | BoundedStringArray
    | BoundedIntMatrix
    | BoundedStringMatrix
)
PreparedProbeStrategy = Literal[
    "WHY",
    "PROVE",
    "ASSUMPTION_CHALLENGE",
    "COUNTEREXAMPLE",
    "COMPLEXITY",
    "EDGE_CASE",
    "TRADE_OFF",
    "ALTERNATIVE",
    "IMPLEMENTATION_CHOICE",
    "CONSTRAINT_MUTATION",
    "FAILURE_MODE",
    "TRANSFER",
]
PreparedInterviewLevel = Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
PreparedInterviewStage = Literal[
    "SETUP",
    "INTRODUCTION",
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DISCOVERY",
    "APPROACH_DEFENSE",
    "IMPLEMENTATION",
    "TESTING_DEBUGGING",
    "COMPLEXITY_EDGE_CASES",
    "CONSTRAINT_MUTATION",
    "FINAL_DEFENSE",
    "WRAP_UP",
    "COMPLETED",
]


class PreparedPrivateArgument(StrictReasoningOutputModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: PreparedCaseValue


class PreparedPrivateCase(StrictReasoningOutputModel):
    arguments: list[PreparedPrivateArgument] = Field(min_length=1, max_length=16)
    expected_output: PreparedCaseValue


class PreparedApproachReference(StrictReasoningOutputModel):
    approach_kind: Literal["EXPECTED", "ALTERNATIVE"]
    approach_index: BoundedReferenceIndex


class PreparedApproachDraft(StrictReasoningOutputModel):
    summary: BoundedSemanticText
    concept_keys: list[BoundedConceptKey] = Field(min_length=1, max_length=8)
    applicability: BoundedSemanticText
    assumptions: list[BoundedSemanticText] = Field(max_length=16)
    key_invariants: list[BoundedSemanticText] = Field(max_length=16)
    time_complexity: BoundedSemanticText
    space_complexity: BoundedSemanticText
    tradeoffs: list[BoundedSemanticText] = Field(max_length=16)
    common_implementation_variants: list[BoundedSemanticText] = Field(max_length=16)
    common_failure_modes: list[BoundedSemanticText] = Field(max_length=16)


class PreparedReferenceSolutionDraft(StrictReasoningOutputModel):
    source_code: BoundedSourceCode
    implementation_notes: BoundedSemanticText | None


class PreparedPrimaryReferenceSolutions(StrictReasoningOutputModel):
    cpp: PreparedReferenceSolutionDraft
    python: PreparedReferenceSolutionDraft
    java: PreparedReferenceSolutionDraft


class PreparedTechnicalItemDraft(StrictReasoningOutputModel):
    concept_keys: list[BoundedConceptKey] = Field(min_length=1, max_length=8)
    diagnostic_goal: BoundedSemanticText
    counterexample_index: BoundedReferenceIndex | None
    approach_reference: PreparedApproachReference | None


class PreparedProbeOpportunityDraft(PreparedTechnicalItemDraft):
    relevant_strategies: list[PreparedProbeStrategy] = Field(min_length=1, max_length=12)


class PreparedCounterexampleDraft(StrictReasoningOutputModel):
    input: BoundedSemanticText
    purpose: BoundedSemanticText


class PreparedCommonFollowupDraft(StrictReasoningOutputModel):
    target_concepts: list[BoundedConceptKey] = Field(min_length=1, max_length=8)
    approach_reference: PreparedApproachReference | None
    trigger_cues: list[BoundedSemanticText] = Field(max_length=16)
    diagnostic_goal: BoundedSemanticText
    relevant_strategies: list[PreparedProbeStrategy] = Field(min_length=1, max_length=12)
    expected_good_signals: list[BoundedSemanticText] = Field(max_length=16)
    weak_or_misconception_signals: list[BoundedSemanticText] = Field(max_length=16)
    counterexample_index: BoundedReferenceIndex | None
    applicable_levels: list[PreparedInterviewLevel] = Field(max_length=3)
    applicable_stages: list[PreparedInterviewStage] = Field(max_length=12)
    sample_phrasings: list[BoundedSemanticText] = Field(max_length=16)


class PreparedLevelConsiderationDraft(StrictReasoningOutputModel):
    level: PreparedInterviewLevel
    guidance: BoundedSemanticText


class PreparedInterviewPackDraft(StrictReasoningOutputModel):
    expected_approaches: list[PreparedApproachDraft] = Field(min_length=1, max_length=4)
    alternative_approaches: list[PreparedApproachDraft] = Field(max_length=4)
    primary_reference_solutions: PreparedPrimaryReferenceSolutions
    concepts: list[BoundedConceptKey] = Field(min_length=1, max_length=8)
    invariants: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    complexity_expectations: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    common_misconceptions: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    failure_modes: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    edge_cases: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    counterexamples: list[PreparedCounterexampleDraft] = Field(max_length=32)
    constraint_mutations: list[PreparedTechnicalItemDraft] = Field(max_length=16)
    probe_opportunities: list[PreparedProbeOpportunityDraft] = Field(max_length=16)
    common_followups: list[PreparedCommonFollowupDraft] = Field(max_length=16)
    level_considerations: list[PreparedLevelConsiderationDraft] = Field(max_length=3)
    reference_reasoning: BoundedSemanticText


class PreparedPackOutput(PreparedInterviewPackDraft):
    private_cases: list[PreparedPrivateCase] = Field(min_length=1, max_length=32)


PackArtifactIssueCode = Literal[
    "PACK_SCHEMA_INVALID",
    "PACK_CONCEPT_INVALID",
    "PRIVATE_CASE_ARGUMENT_MISMATCH",
    "PRIVATE_CASE_VALUE_TYPE_INVALID",
    "PRIVATE_CASE_EXPECTED_OUTPUT_TYPE_INVALID",
]


@dataclass(frozen=True)
class PackArtifactIssue:
    code: PackArtifactIssueCode
    field: str
    expected_semantic_type: SemanticType | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,160}", self.field) is None:
            object.__setattr__(self, "field", "pack")


class PackArtifactValidationError(ValueError):
    def __init__(self, issues: Sequence[PackArtifactIssue]) -> None:
        if not issues:
            raise ValueError("Pack artifact failure requires a bounded issue")
        self.issues = _bounded_unique_issues(issues)
        super().__init__("Prepared pack artifacts failed deterministic validation")


def validate_prepared_pack_artifacts(
    *,
    semantic_pack: PreparedInterviewPackDraft,
    private_cases: Sequence[PreparedPrivateCase],
    problem: ProblemContent,
    active_concept_keys: Set[str],
) -> tuple[InterviewPackContent, list[VisibleCase]]:
    """Validate the model output without logging or exposing its raw contents."""

    pack = build_custom_interview_pack(
        semantic_pack=semantic_pack,
        active_concept_keys=active_concept_keys,
    )
    try:
        CuratedContent(problem=problem, interview_pack=pack)
    except ValidationError as exc:
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", _validation_field("pack", exc))]
        ) from exc

    argument_types = {item.name: item.type for item in problem.execution.arguments}
    validated_cases: list[VisibleCase] = []
    issues: list[PackArtifactIssue] = []
    for index, candidate in enumerate(private_cases):
        path = f"private_cases[{index}]"
        arguments = {item.name: item.value for item in candidate.arguments}
        if len(arguments) != len(candidate.arguments) or set(arguments) != set(argument_types):
            issues.append(
                PackArtifactIssue("PRIVATE_CASE_ARGUMENT_MISMATCH", f"{path}.arguments")
            )
            continue
        for name, semantic_type in argument_types.items():
            if not validate_semantic_value(arguments[name], semantic_type):
                issues.append(
                    PackArtifactIssue(
                        "PRIVATE_CASE_VALUE_TYPE_INVALID",
                        f"{path}.arguments.{name}",
                        expected_semantic_type=semantic_type,
                    )
                )
        if not validate_semantic_value(
            candidate.expected_output, problem.execution.return_type
        ):
            issues.append(
                PackArtifactIssue(
                    "PRIVATE_CASE_EXPECTED_OUTPUT_TYPE_INVALID",
                    f"{path}.expected_output",
                    expected_semantic_type=problem.execution.return_type,
                )
            )
        if not issues or all(not issue.field.startswith(path) for issue in issues):
            validated_cases.append(
                VisibleCase(arguments=arguments, expected_output=candidate.expected_output)
            )
    if issues:
        raise PackArtifactValidationError(issues)
    return pack, validated_cases


def build_custom_interview_pack(
    *,
    semantic_pack: PreparedInterviewPackDraft,
    active_concept_keys: Set[str],
) -> InterviewPackContent:
    """Assign storage mechanics and validate the final immutable pack in software."""

    if not set(semantic_pack.concepts).issubset(active_concept_keys):
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_CONCEPT_INVALID", "pack.concepts")]
        )

    expected_ids = [
        f"expected_approach_{index}"
        for index in range(1, len(semantic_pack.expected_approaches) + 1)
    ]
    alternative_ids = [
        f"alternative_approach_{index}"
        for index in range(1, len(semantic_pack.alternative_approaches) + 1)
    ]
    counterexample_ids = [
        f"counterexample_{index}"
        for index in range(1, len(semantic_pack.counterexamples) + 1)
    ]

    raw_pack: dict[str, object] = {
        "schema_version": "interview-pack.v1",
        "version": "v1",
        "review_status": "REVIEWED",
        "expected_approaches": [
            _approach_payload(item, approach_id)
            for item, approach_id in zip(
                semantic_pack.expected_approaches,
                expected_ids,
                strict=True,
            )
        ],
        "alternative_approaches": [
            _approach_payload(item, approach_id)
            for item, approach_id in zip(
                semantic_pack.alternative_approaches,
                alternative_ids,
                strict=True,
            )
        ],
        "reference_solutions": _reference_solution_payloads(
            semantic_pack.primary_reference_solutions,
            expected_ids[0],
        ),
        "concepts": semantic_pack.concepts,
        "invariants": _technical_payloads(
            semantic_pack.invariants,
            namespace="invariant",
            path="pack.invariants",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "complexity_expectations": _technical_payloads(
            semantic_pack.complexity_expectations,
            namespace="complexity",
            path="pack.complexity_expectations",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "common_misconceptions": _technical_payloads(
            semantic_pack.common_misconceptions,
            namespace="misconception",
            path="pack.common_misconceptions",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "failure_modes": _technical_payloads(
            semantic_pack.failure_modes,
            namespace="failure_mode",
            path="pack.failure_modes",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "edge_cases": _technical_payloads(
            semantic_pack.edge_cases,
            namespace="edge_case",
            path="pack.edge_cases",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "counterexamples": [
            {
                "id": counterexample_id,
                "input": item.input,
                "purpose": item.purpose,
            }
            for item, counterexample_id in zip(
                semantic_pack.counterexamples,
                counterexample_ids,
                strict=True,
            )
        ],
        "constraint_mutations": _technical_payloads(
            semantic_pack.constraint_mutations,
            namespace="constraint_mutation",
            path="pack.constraint_mutations",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "probe_opportunities": _probe_payloads(
            semantic_pack.probe_opportunities,
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "common_followups": _followup_payloads(
            semantic_pack.common_followups,
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        ),
        "level_considerations": [
            item.model_dump(mode="json") for item in semantic_pack.level_considerations
        ],
        "reference_reasoning": semantic_pack.reference_reasoning,
    }
    try:
        return InterviewPackContent.model_validate(raw_pack)
    except ValidationError as exc:
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", _validation_field("pack", exc))]
        ) from exc


def _approach_payload(item: PreparedApproachDraft, approach_id: str) -> dict[str, object]:
    return {"approach_id": approach_id, **item.model_dump(mode="json")}


def _reference_solution_payloads(
    references: PreparedPrimaryReferenceSolutions,
    primary_approach_id: str,
) -> list[dict[str, object]]:
    return [
        {
            "approach_id": primary_approach_id,
            "language": language,
            "source_code": reference.source_code,
            "review_status": "REVIEWED",
            "implementation_notes": reference.implementation_notes,
        }
        for language, reference in (
            ("cpp", references.cpp),
            ("python", references.python),
            ("java", references.java),
        )
    ]


def _technical_payloads(
    items: Sequence[PreparedTechnicalItemDraft],
    *,
    namespace: str,
    path: str,
    expected_ids: Sequence[str],
    alternative_ids: Sequence[str],
    counterexample_ids: Sequence[str],
) -> list[dict[str, object]]:
    return [
        _technical_payload(
            item,
            canonical_id=f"{namespace}_{index + 1}",
            path=f"{path}[{index}]",
            expected_ids=expected_ids,
            alternative_ids=alternative_ids,
            counterexample_ids=counterexample_ids,
        )
        for index, item in enumerate(items)
    ]


def _technical_payload(
    item: PreparedTechnicalItemDraft,
    *,
    canonical_id: str,
    path: str,
    expected_ids: Sequence[str],
    alternative_ids: Sequence[str],
    counterexample_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "id": canonical_id,
        "concept_keys": item.concept_keys,
        "diagnostic_goal": item.diagnostic_goal,
        "counterexample_id": _resolve_counterexample_reference(
            item.counterexample_index,
            counterexample_ids,
            path=f"{path}.counterexample_index",
        ),
        "approach_id": _resolve_approach_reference(
            item.approach_reference,
            expected_ids,
            alternative_ids,
            path=f"{path}.approach_reference",
        ),
    }


def _probe_payloads(
    items: Sequence[PreparedProbeOpportunityDraft],
    *,
    expected_ids: Sequence[str],
    alternative_ids: Sequence[str],
    counterexample_ids: Sequence[str],
) -> list[dict[str, object]]:
    return [
        {
            **_technical_payload(
                item,
                canonical_id=f"probe_{index + 1}",
                path=f"pack.probe_opportunities[{index}]",
                expected_ids=expected_ids,
                alternative_ids=alternative_ids,
                counterexample_ids=counterexample_ids,
            ),
            "relevant_strategies": item.relevant_strategies,
        }
        for index, item in enumerate(items)
    ]


def _followup_payloads(
    items: Sequence[PreparedCommonFollowupDraft],
    *,
    expected_ids: Sequence[str],
    alternative_ids: Sequence[str],
    counterexample_ids: Sequence[str],
) -> list[dict[str, object]]:
    return [
        {
            "id": f"followup_{index + 1}",
            "target_concepts": item.target_concepts,
            "target_approach_id": _resolve_approach_reference(
                item.approach_reference,
                expected_ids,
                alternative_ids,
                path=f"pack.common_followups[{index}].approach_reference",
            ),
            "trigger_cues": item.trigger_cues,
            "diagnostic_goal": item.diagnostic_goal,
            "relevant_strategies": item.relevant_strategies,
            "expected_good_signals": item.expected_good_signals,
            "weak_or_misconception_signals": item.weak_or_misconception_signals,
            "counterexample_id": _resolve_counterexample_reference(
                item.counterexample_index,
                counterexample_ids,
                path=f"pack.common_followups[{index}].counterexample_index",
            ),
            "applicable_levels": item.applicable_levels,
            "applicable_stages": item.applicable_stages,
            "sample_phrasings": item.sample_phrasings,
        }
        for index, item in enumerate(items)
    ]


def _resolve_approach_reference(
    reference: PreparedApproachReference | None,
    expected_ids: Sequence[str],
    alternative_ids: Sequence[str],
    *,
    path: str,
) -> str | None:
    if reference is None:
        return None
    candidates = expected_ids if reference.approach_kind == "EXPECTED" else alternative_ids
    if reference.approach_index >= len(candidates):
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", path)]
        )
    return candidates[reference.approach_index]


def _resolve_counterexample_reference(
    index: int | None,
    counterexample_ids: Sequence[str],
    *,
    path: str,
) -> str | None:
    if index is None:
        return None
    if index >= len(counterexample_ids):
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", path)]
        )
    return counterexample_ids[index]


def _validation_field(prefix: str, exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    if not errors:
        return prefix
    location = ".".join(str(part) for part in errors[0].get("loc", ()))
    candidate = f"{prefix}.{location}" if location else prefix
    return candidate[:160]


def _bounded_unique_issues(issues: Sequence[PackArtifactIssue]) -> tuple[PackArtifactIssue, ...]:
    unique: list[PackArtifactIssue] = []
    seen: set[PackArtifactIssue] = set()
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
        if len(unique) == 8:
            break
    return tuple(unique)

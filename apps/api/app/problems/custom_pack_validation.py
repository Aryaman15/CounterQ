"""Bounded validation for strong-model Interview Pack and private-case output."""

from __future__ import annotations

import json
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


class PreparedPrivateArgument(StrictReasoningOutputModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: PreparedCaseValue


class PreparedPrivateCase(StrictReasoningOutputModel):
    arguments: list[PreparedPrivateArgument] = Field(min_length=1, max_length=16)
    expected_output: PreparedCaseValue


PackArtifactIssueCode = Literal[
    "PACK_JSON_INVALID",
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
    pack_json: str,
    private_cases: Sequence[PreparedPrivateCase],
    problem: ProblemContent,
    active_concept_keys: Set[str],
) -> tuple[InterviewPackContent, list[VisibleCase]]:
    """Validate the model output without logging or exposing its raw contents."""

    try:
        raw_pack = json.loads(pack_json)
    except json.JSONDecodeError as exc:
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_JSON_INVALID", "pack_json")]
        ) from exc
    if not isinstance(raw_pack, dict):
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", "pack")]
        )
    raw_pack.update(
        {
            "schema_version": "interview-pack.v1",
            "version": "v1",
            "review_status": "REVIEWED",
        }
    )
    try:
        pack = InterviewPackContent.model_validate(raw_pack)
    except ValidationError as exc:
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_SCHEMA_INVALID", _validation_field("pack", exc))]
        ) from exc
    if not set(pack.concepts).issubset(active_concept_keys):
        raise PackArtifactValidationError(
            [PackArtifactIssue("PACK_CONCEPT_INVALID", "pack.concepts")]
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

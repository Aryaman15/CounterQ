"""Bounded validation diagnostics for model-proposed custom problem artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.problems.content import (
    ExecutionDefinition,
    ProblemContent,
    SemanticType,
    VisibleCase,
    validate_semantic_value,
)
from app.problems.custom_source_evidence import CustomProblemSourceEvidence
from app.problems.starter_scaffolds import starter_languages_for_execution

NormalizationArtifactIssueCode = Literal[
    "PROBLEM_JSON_INVALID",
    "PROBLEM_SCHEMA_INVALID",
    "COMPARATOR_INVALID",
    "EXECUTION_SIGNATURE_CONFLICT",
    "VISIBLE_CASE_ARGUMENT_MISMATCH",
    "VISIBLE_CASE_VALUE_TYPE_INVALID",
    "EXPECTED_OUTPUT_TYPE_INVALID",
    "PRIVATE_CASES_JSON_INVALID",
    "PRIVATE_CASES_SCHEMA_INVALID",
    "PRIVATE_CASES_EMPTY",
    "PRIVATE_CASE_ARGUMENT_MISMATCH",
    "PRIVATE_CASE_VALUE_TYPE_INVALID",
    "UNKNOWN_CONCEPT_KEY",
]

_SAFE_FIELD_PATH = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,160}$")
_SAFE_CONCEPT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SCALAR_RETURN_TYPES = frozenset({"int", "bool", "string"})


@dataclass(frozen=True)
class NormalizationArtifactIssue:
    code: NormalizationArtifactIssueCode
    field: str
    expected_semantic_type: SemanticType | None = None
    unknown_concept_key: str | None = None

    def __post_init__(self) -> None:
        if _SAFE_FIELD_PATH.fullmatch(self.field) is None:
            object.__setattr__(self, "field", "artifact")
        if (
            self.unknown_concept_key is not None
            and _SAFE_CONCEPT_KEY.fullmatch(self.unknown_concept_key) is None
        ):
            object.__setattr__(self, "unknown_concept_key", None)

    def to_payload(self) -> dict[str, str]:
        payload = {"code": self.code, "field": self.field}
        if self.expected_semantic_type is not None:
            payload["expected_semantic_type"] = self.expected_semantic_type
        if self.unknown_concept_key is not None:
            payload["unknown_concept_key"] = self.unknown_concept_key
        return payload


class NormalizationArtifactValidationError(ValueError):
    def __init__(self, issues: Sequence[NormalizationArtifactIssue]) -> None:
        if not issues:
            raise ValueError("Normalization artifact failure requires a bounded issue")
        self.issues = _bounded_unique_issues(issues)
        super().__init__("Normalized artifacts failed deterministic validation")


def validate_normalization_artifacts(
    problem_json: str,
    private_cases_json: str,
    *,
    preparation_id: UUID,
    source_evidence: CustomProblemSourceEvidence,
    active_concept_keys: Set[str],
) -> tuple[ProblemContent, list[VisibleCase]]:
    """Validate generated drafts without exposing raw output or exception text."""

    try:
        raw_problem = json.loads(problem_json)
    except json.JSONDecodeError as exc:
        raise NormalizationArtifactValidationError(
            [NormalizationArtifactIssue("PROBLEM_JSON_INVALID", "problem_json")]
        ) from exc
    if not isinstance(raw_problem, dict):
        raise NormalizationArtifactValidationError(
            [NormalizationArtifactIssue("PROBLEM_SCHEMA_INVALID", "problem")]
        )

    model_execution = raw_problem.get("execution")
    source_execution = authoritative_source_execution(
        source_evidence,
        model_execution=model_execution,
    )
    raw_execution = (
        source_execution
        if source_execution is not None
        else canonicalize_execution_comparator(model_execution)
    )
    try:
        execution = ExecutionDefinition.model_validate(raw_execution)
    except ValidationError as exc:
        raise NormalizationArtifactValidationError([_execution_validation_issue(exc)]) from exc

    issues: list[NormalizationArtifactIssue] = []
    signature_issue = execution_signature_issue(execution, source_evidence)
    if signature_issue is not None:
        issues.append(signature_issue)
    issues.extend(
        _case_issues(
            execution.visible_cases,
            execution,
            path_prefix="problem.execution.visible_cases",
            argument_mismatch_code="VISIBLE_CASE_ARGUMENT_MISMATCH",
            value_type_code="VISIBLE_CASE_VALUE_TYPE_INVALID",
        )
    )
    if issues:
        raise NormalizationArtifactValidationError(issues)

    try:
        languages = starter_languages_for_execution(execution)
    except ValueError as exc:
        raise NormalizationArtifactValidationError(
            [NormalizationArtifactIssue("PROBLEM_SCHEMA_INVALID", "problem.execution.method_name")]
        ) from exc

    raw_problem.update(
        {
            "schema_version": "problem.v1",
            "slug": f"custom-{preparation_id}",
            "version": "v1",
            "catalog_order": 1,
            "review_status": "REVIEWED",
            "execution": execution.model_dump(mode="json"),
            "languages": {
                language: definition.model_dump(mode="json")
                for language, definition in languages.items()
            },
        }
    )
    try:
        problem = ProblemContent.model_validate(raw_problem)
    except ValidationError as exc:
        raise NormalizationArtifactValidationError(
            [
                NormalizationArtifactIssue(
                    "PROBLEM_SCHEMA_INVALID",
                    _validation_field("problem", exc),
                )
            ]
        ) from exc

    unknown_concepts = sorted(
        {
            mapping.canonical_key
            for mapping in problem.problem_concepts
            if mapping.canonical_key not in active_concept_keys
        }
    )
    if unknown_concepts:
        raise NormalizationArtifactValidationError(
            [
                NormalizationArtifactIssue(
                    "UNKNOWN_CONCEPT_KEY",
                    "problem.problem_concepts",
                    unknown_concept_key=key,
                )
                for key in unknown_concepts
            ]
        )

    try:
        raw_private_cases = json.loads(private_cases_json)
    except json.JSONDecodeError as exc:
        raise NormalizationArtifactValidationError(
            [NormalizationArtifactIssue("PRIVATE_CASES_JSON_INVALID", "private_cases_json")]
        ) from exc
    try:
        private_cases = TypeAdapter(list[VisibleCase]).validate_python(raw_private_cases)
    except ValidationError as exc:
        raise NormalizationArtifactValidationError(
            [
                NormalizationArtifactIssue(
                    "PRIVATE_CASES_SCHEMA_INVALID",
                    _validation_field("private_cases", exc),
                )
            ]
        ) from exc
    if not private_cases:
        raise NormalizationArtifactValidationError(
            [NormalizationArtifactIssue("PRIVATE_CASES_EMPTY", "private_cases")]
        )

    private_issues = _case_issues(
        private_cases,
        execution,
        path_prefix="private_cases",
        argument_mismatch_code="PRIVATE_CASE_ARGUMENT_MISMATCH",
        value_type_code="PRIVATE_CASE_VALUE_TYPE_INVALID",
    )
    if private_issues:
        raise NormalizationArtifactValidationError(private_issues)
    return problem, private_cases


def canonicalize_execution_comparator(raw_execution: object) -> object:
    """Make scalar equality software-owned without inferring collection semantics."""

    if not isinstance(raw_execution, dict):
        return raw_execution
    if raw_execution.get("return_type") not in _SCALAR_RETURN_TYPES:
        return raw_execution
    canonical = dict(raw_execution)
    canonical["comparator"] = "EXACT"
    return canonical


def authoritative_source_execution(
    source_evidence: CustomProblemSourceEvidence,
    *,
    model_execution: object,
) -> dict[str, object] | None:
    """Construct source-owned mechanics only from a complete signature and typed cases."""

    signature = source_evidence.signature
    if signature is None or not source_evidence.parsed_visible_cases:
        return None
    comparator: object
    if signature.return_type in _SCALAR_RETURN_TYPES:
        comparator = "EXACT"
    elif isinstance(model_execution, dict):
        comparator = model_execution.get("comparator")
    else:
        comparator = None
    return {
        "method_name": signature.method_name,
        "arguments": [argument.to_payload() for argument in signature.arguments],
        "return_type": signature.return_type,
        "comparator": comparator,
        "visible_cases": [case.to_payload() for case in source_evidence.parsed_visible_cases],
        "custom_test_supported": True,
    }


def execution_signature_issue(
    execution: ExecutionDefinition,
    source_evidence: CustomProblemSourceEvidence,
) -> NormalizationArtifactIssue | None:
    """Require exact agreement only when software parsed a complete source signature."""

    signature = source_evidence.signature
    if signature is None:
        return None
    expected_arguments = tuple((argument.name, argument.type) for argument in signature.arguments)
    actual_arguments = tuple((argument.name, argument.type) for argument in execution.arguments)
    if (
        execution.method_name != signature.method_name
        or actual_arguments != expected_arguments
        or execution.return_type != signature.return_type
    ):
        return NormalizationArtifactIssue("EXECUTION_SIGNATURE_CONFLICT", "problem.execution")
    return None


def _case_issues(
    cases: Sequence[VisibleCase],
    execution: ExecutionDefinition,
    *,
    path_prefix: str,
    argument_mismatch_code: Literal[
        "VISIBLE_CASE_ARGUMENT_MISMATCH", "PRIVATE_CASE_ARGUMENT_MISMATCH"
    ],
    value_type_code: Literal["VISIBLE_CASE_VALUE_TYPE_INVALID", "PRIVATE_CASE_VALUE_TYPE_INVALID"],
) -> list[NormalizationArtifactIssue]:
    argument_types = {argument.name: argument.type for argument in execution.arguments}
    issues: list[NormalizationArtifactIssue] = []
    for index, case in enumerate(cases):
        case_path = f"{path_prefix}[{index}]"
        if set(case.arguments) != set(argument_types):
            issues.append(
                NormalizationArtifactIssue(argument_mismatch_code, f"{case_path}.arguments")
            )
        for name, semantic_type in argument_types.items():
            if name in case.arguments and not validate_semantic_value(
                case.arguments[name], semantic_type
            ):
                issues.append(
                    NormalizationArtifactIssue(
                        value_type_code,
                        f"{case_path}.arguments.{name}",
                        expected_semantic_type=semantic_type,
                    )
                )
        if not validate_semantic_value(case.expected_output, execution.return_type):
            issues.append(
                NormalizationArtifactIssue(
                    "EXPECTED_OUTPUT_TYPE_INVALID",
                    f"{case_path}.expected_output",
                    expected_semantic_type=execution.return_type,
                )
            )
    return issues


def _validation_field(prefix: str, exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    if not errors:
        return prefix
    location = ".".join(str(part) for part in errors[0].get("loc", ()))
    return f"{prefix}.{location}" if location else prefix


def _execution_validation_issue(exc: ValidationError) -> NormalizationArtifactIssue:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    if any(tuple(error.get("loc", ())) == ("comparator",) for error in errors):
        return NormalizationArtifactIssue("COMPARATOR_INVALID", "problem.execution.comparator")
    return NormalizationArtifactIssue(
        "PROBLEM_SCHEMA_INVALID",
        _validation_field("problem.execution", exc),
    )


def _bounded_unique_issues(
    issues: Sequence[NormalizationArtifactIssue],
) -> tuple[NormalizationArtifactIssue, ...]:
    unique: list[NormalizationArtifactIssue] = []
    seen: set[NormalizationArtifactIssue] = set()
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
        if len(unique) == 8:
            break
    return tuple(unique)

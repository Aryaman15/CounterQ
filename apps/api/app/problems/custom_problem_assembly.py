"""Deterministic ProblemContent assembly for trusted custom-problem preparation."""

from __future__ import annotations

import re
from collections.abc import Sequence, Set
from uuid import UUID

from app.problems.content import (
    ArgumentDefinition,
    ExecutionDefinition,
    ProblemConceptDefinition,
    ProblemContent,
    ProblemExample,
    VisibleCase,
)
from app.problems.custom_source_evidence import CustomProblemSourceEvidence
from app.problems.starter_scaffolds import starter_languages_for_execution


class CustomProblemAssemblyNeedsCorrection(ValueError):
    def __init__(self, finding_codes: tuple[str, ...]) -> None:
        self.finding_codes = finding_codes
        super().__init__("Custom problem source is missing required executable content")


class CustomProblemAssemblyValidationError(ValueError):
    """A semantic normalization draft violated a trusted software constraint."""


def build_custom_problem_content(
    *,
    preparation_id: UUID,
    source_evidence: CustomProblemSourceEvidence,
    title: str | None,
    normalized_statement: str | None,
    normalized_constraints: Sequence[str],
    problem_concepts: Sequence[ProblemConceptDefinition],
    active_concept_keys: Set[str],
) -> ProblemContent:
    """Assemble the final strict artifact; AI supplies semantics but never mechanics."""

    signature = source_evidence.signature
    missing: list[str] = []
    if signature is None:
        missing.append("MISSING_ARGUMENTS")
    if not source_evidence.parsed_visible_cases:
        missing.append("MISSING_EXAMPLE")

    statement = source_evidence.statement or _bounded_fallback_statement(normalized_statement)
    if statement is None:
        missing.append("MISSING_PROBLEM_TEXT")
    constraints = source_evidence.constraints or _bounded_fallback_constraints(
        normalized_constraints
    )
    if not constraints:
        missing.append("MISSING_CONSTRAINTS")
    if missing:
        raise CustomProblemAssemblyNeedsCorrection(tuple(dict.fromkeys(missing)))
    assert signature is not None
    assert statement is not None

    concept_keys = [item.canonical_key for item in problem_concepts]
    if len(concept_keys) != len(set(concept_keys)):
        raise CustomProblemAssemblyValidationError("Concept selections must be unique")
    if not set(concept_keys).issubset(active_concept_keys):
        raise CustomProblemAssemblyValidationError(
            "Concept selections must come from the active ontology"
        )

    execution = ExecutionDefinition(
        method_name=signature.method_name,
        arguments=[
            ArgumentDefinition(name=argument.name, type=argument.type)
            for argument in signature.arguments
        ],
        return_type=signature.return_type,
        comparator="EXACT",
        visible_cases=[
            VisibleCase(arguments=dict(example.arguments), expected_output=example.expected_output)
            for example in source_evidence.parsed_visible_cases
        ],
        custom_test_supported=True,
    )
    return ProblemContent.model_validate(
        {
            "schema_version": "problem.v1",
            "slug": f"custom-{preparation_id}",
            "version": "v1",
            "catalog_order": 1,
            "title": _prepared_title(title, signature.method_name),
            "review_status": "REVIEWED",
            "statement": statement,
            "constraints": list(constraints),
            "examples": [
                ProblemExample(
                    input=example.input_text,
                    output=example.output_text,
                    explanation=example.explanation,
                ).model_dump(mode="json")
                for example in source_evidence.parsed_visible_cases
            ],
            "execution": execution.model_dump(mode="json"),
            "languages": {
                language: definition.model_dump(mode="json")
                for language, definition in starter_languages_for_execution(execution).items()
            },
            "problem_concepts": [
                selection.model_dump(mode="json") for selection in problem_concepts
            ],
        }
    )


def fallback_title_from_method(method_name: str) -> str:
    words = re.sub(r"[_-]+", " ", method_name)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
    words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", words)
    return " ".join(word.capitalize() for word in words.split())


def execution_signature_conflicts_with_source(
    execution: ExecutionDefinition,
    source_evidence: CustomProblemSourceEvidence,
) -> bool:
    """Revalidate an immutable artifact against any complete source signature."""

    signature = source_evidence.signature
    if signature is None:
        return False
    expected_arguments = tuple((item.name, item.type) for item in signature.arguments)
    actual_arguments = tuple((item.name, item.type) for item in execution.arguments)
    return (
        execution.method_name != signature.method_name
        or actual_arguments != expected_arguments
        or execution.return_type != signature.return_type
    )


def _prepared_title(title: str | None, method_name: str) -> str:
    proposed = title.strip() if title is not None else ""
    return proposed or fallback_title_from_method(method_name)


def _bounded_fallback_statement(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= 20_000 else None


def _bounded_fallback_constraints(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values if value.strip())
    if not normalized or len(normalized) > 32 or any(len(value) > 500 for value in normalized):
        return ()
    return normalized

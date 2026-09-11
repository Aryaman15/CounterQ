from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest

from app.problems.content import load_curated_content
from app.problems.custom_artifact_validation import (
    NormalizationArtifactIssue,
    NormalizationArtifactValidationError,
    validate_normalization_artifacts,
)
from app.problems.custom_source_evidence import derive_custom_problem_source_evidence

SOURCE = """Count Pairs
Given nums and target, return the number of matching index pairs.
Function signature: int countPairs(vector<int> nums, int target)
Example:
Input: nums = [1, 2, 3, 4], target = 5
Output: 2
"""


def _artifacts() -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    entry = next(item for item in load_curated_content() if item.problem.slug == "two-sum")
    problem = entry.problem.model_dump(mode="json")
    problem["execution"] = {
        "method_name": "countPairs",
        "arguments": [
            {"name": "nums", "type": "int[]"},
            {"name": "target", "type": "int"},
        ],
        "return_type": "int",
        "comparator": "EXACT",
        "visible_cases": [
            {
                "arguments": {"nums": [1, 2, 3, 4], "target": 5},
                "expected_output": 2,
            }
        ],
        "custom_test_supported": True,
    }
    private_cases = [
        {
            "arguments": {"nums": [1, 1, 1], "target": 2},
            "expected_output": 3,
        }
    ]
    concepts = {item.canonical_key for item in entry.problem.problem_concepts}
    return problem, private_cases, concepts


def _validate(
    problem: object,
    private_cases: object,
    concepts: set[str],
) -> None:
    validate_normalization_artifacts(
        problem if isinstance(problem, str) else json.dumps(problem),
        private_cases if isinstance(private_cases, str) else json.dumps(private_cases),
        preparation_id=uuid4(),
        source_evidence=derive_custom_problem_source_evidence(SOURCE),
        active_concept_keys=concepts,
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("problem_json_invalid", "PROBLEM_JSON_INVALID"),
        ("problem_schema_invalid", "PROBLEM_SCHEMA_INVALID"),
        ("signature_conflict", "EXECUTION_SIGNATURE_CONFLICT"),
        ("visible_arguments", "VISIBLE_CASE_ARGUMENT_MISMATCH"),
        ("visible_value_type", "VISIBLE_CASE_VALUE_TYPE_INVALID"),
        ("expected_output_type", "EXPECTED_OUTPUT_TYPE_INVALID"),
        ("private_json_invalid", "PRIVATE_CASES_JSON_INVALID"),
        ("private_schema_invalid", "PRIVATE_CASES_SCHEMA_INVALID"),
        ("private_empty", "PRIVATE_CASES_EMPTY"),
        ("private_arguments", "PRIVATE_CASE_ARGUMENT_MISMATCH"),
        ("private_value_type", "PRIVATE_CASE_VALUE_TYPE_INVALID"),
        ("unknown_concept", "UNKNOWN_CONCEPT_KEY"),
    ],
)
def test_normalized_artifact_failures_have_bounded_diagnostic_codes(
    mutation: str,
    expected_code: str,
) -> None:
    problem, private_cases, concepts = _artifacts()
    problem_input: Any = deepcopy(problem)
    private_input: Any = deepcopy(private_cases)
    if mutation == "problem_json_invalid":
        problem_input = "{"
    elif mutation == "problem_schema_invalid":
        del problem_input["execution"]
    elif mutation == "signature_conflict":
        problem_input["execution"]["method_name"] = "solve"
    elif mutation == "visible_arguments":
        problem_input["execution"]["visible_cases"][0]["arguments"] = {
            "values": [1, 2, 3, 4],
            "target": 5,
        }
    elif mutation == "visible_value_type":
        problem_input["execution"]["visible_cases"][0]["arguments"]["target"] = "5"
    elif mutation == "expected_output_type":
        problem_input["execution"]["visible_cases"][0]["expected_output"] = "2"
    elif mutation == "private_json_invalid":
        private_input = "["
    elif mutation == "private_schema_invalid":
        private_input = {"not": "a list"}
    elif mutation == "private_empty":
        private_input = []
    elif mutation == "private_arguments":
        private_input[0]["arguments"] = {
            "values": [1, 1, 1],
            "target": 2,
        }
    elif mutation == "private_value_type":
        private_input[0]["arguments"]["target"] = "2"
    else:
        problem_input["problem_concepts"][0]["canonical_key"] = "invented"

    with pytest.raises(NormalizationArtifactValidationError) as caught:
        _validate(problem_input, private_input, concepts)

    assert expected_code in {issue.code for issue in caught.value.issues}
    assert 1 <= len(caught.value.issues) <= 8
    assert all(issue.field for issue in caught.value.issues)


def test_valid_artifacts_preserve_the_exact_source_signature() -> None:
    problem, private_cases, concepts = _artifacts()

    validated, validated_private_cases = validate_normalization_artifacts(
        json.dumps(problem),
        json.dumps(private_cases),
        preparation_id=uuid4(),
        source_evidence=derive_custom_problem_source_evidence(SOURCE),
        active_concept_keys=concepts,
    )

    assert validated.execution.method_name == "countPairs"
    assert [(item.name, item.type) for item in validated.execution.arguments] == [
        ("nums", "int[]"),
        ("target", "int"),
    ]
    assert validated.execution.return_type == "int"
    assert len(validated_private_cases) == 1


def test_diagnostic_payload_drops_unbounded_or_unsafe_generated_labels() -> None:
    issue = NormalizationArtifactIssue(
        "UNKNOWN_CONCEPT_KEY",
        "problem.candidate-controlled field",
        unknown_concept_key="x" * 1_000,
    )

    assert issue.to_payload() == {
        "code": "UNKNOWN_CONCEPT_KEY",
        "field": "artifact",
    }

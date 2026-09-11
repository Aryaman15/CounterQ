from __future__ import annotations

import pytest

from app.problems.custom_source_evidence import (
    contradicted_normalization_findings,
    derive_custom_problem_source_evidence,
    parse_supported_function_signature,
)

EXPECTED_ALL_ARGUMENT_TYPES = [
    "int",
    "bool",
    "string",
    "int[]",
    "string[]",
    "int[][]",
    "string[][]",
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "int countPairs(const vector<int>& nums, int target)",
            {
                "method_name": "countPairs",
                "arguments": [
                    {"name": "nums", "type": "int[]"},
                    {"name": "target", "type": "int"},
                ],
                "return_type": "int",
            },
        ),
        (
            "def solve(self, grid: list[list[str]], enabled: bool) -> str:",
            {
                "method_name": "solve",
                "arguments": [
                    {"name": "grid", "type": "string[][]"},
                    {"name": "enabled", "type": "bool"},
                ],
                "return_type": "string",
            },
        ),
        (
            "public String[] collect(final int[][] grid, String label)",
            {
                "method_name": "collect",
                "arguments": [
                    {"name": "grid", "type": "int[][]"},
                    {"name": "label", "type": "string"},
                ],
                "return_type": "string[]",
            },
        ),
    ],
)
def test_supported_signature_parser_maps_current_cpp_python_and_java_shapes(
    source: str, expected: dict[str, object]
) -> None:
    signature = parse_supported_function_signature(source)

    assert signature is not None
    assert signature.to_payload() == expected


@pytest.mark.parametrize(
    "source",
    [
        (
            "vector<vector<string>> allTypes(int a, bool b, string c, "
            "vector<int> d, vector<string> e, vector<vector<int>> f, "
            "vector<vector<string>> g)"
        ),
        (
            "def allTypes(self, a: int, b: bool, c: str, d: list[int], "
            "e: list[str], f: list[list[int]], g: list[list[str]]) "
            "-> list[list[str]]:"
        ),
        (
            "public String[][] allTypes(int a, boolean b, String c, int[] d, "
            "String[] e, int[][] f, String[][] g)"
        ),
    ],
)
def test_signature_parser_covers_every_supported_semantic_type(source: str) -> None:
    signature = parse_supported_function_signature(source)

    assert signature is not None
    assert [argument.type for argument in signature.arguments] == EXPECTED_ALL_ARGUMENT_TYPES
    assert signature.return_type == "string[][]"


@pytest.mark.parametrize(
    "source",
    [
        "long solve(vector<int> nums)",
        "int solve(vector<double> nums)",
        "def solve(self, nums) -> int:",
        "int first(int value)\nbool second(bool value)",
    ],
)
def test_signature_parser_returns_no_hint_for_unsupported_or_conflicting_shapes(
    source: str,
) -> None:
    assert parse_supported_function_signature(source) is None


def test_source_evidence_and_contradictions_are_bounded_to_explicit_facts() -> None:
    evidence = derive_custom_problem_source_evidence(
        """Given nums and target, return the number of valid pairs.

Function signature:
int countPairs(vector<int> nums, int target)

Example 1:
Input: nums = [1, 2], target = 3
Output: 1
"""
    )

    assert evidence.to_payload() == {
        "signature": {
            "method_name": "countPairs",
            "arguments": [
                {"name": "nums", "type": "int[]"},
                {"name": "target", "type": "int"},
            ],
            "return_type": "int",
        },
        "has_explicit_return_directive": True,
        "has_example_section": True,
        "has_expected_output_example": True,
    }
    assert contradicted_normalization_findings(
        [
            "MISSING_RETURN_BEHAVIOR",
            "MISSING_ARGUMENTS",
            "AMBIGUOUS_ARGUMENT_TYPES",
            "MISSING_EXAMPLE",
            "CONTRADICTORY_EXAMPLES",
        ],
        evidence,
    ) == (
        "MISSING_RETURN_BEHAVIOR",
        "MISSING_ARGUMENTS",
        "AMBIGUOUS_ARGUMENT_TYPES",
        "MISSING_EXAMPLE",
    )


def test_signature_alone_does_not_claim_missing_return_semantics_are_resolved() -> None:
    evidence = derive_custom_problem_source_evidence(
        "Given nums and target, find a pair.\nint solve(vector<int> nums, int target)"
    )

    assert evidence.signature is not None
    assert not evidence.has_explicit_return_directive
    assert contradicted_normalization_findings(["MISSING_RETURN_BEHAVIOR"], evidence) == ()


def test_example_heading_without_both_labeled_values_is_not_proof() -> None:
    evidence = derive_custom_problem_source_evidence(
        "Example 1:\nInput: nums = [1, 2]\nThe answer is one."
    )

    assert evidence.has_example_section
    assert not evidence.has_expected_output_example
    assert contradicted_normalization_findings(["MISSING_EXAMPLE"], evidence) == ()

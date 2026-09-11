from __future__ import annotations

import pytest

from app.problems.custom_source_evidence import (
    contradicted_normalization_findings,
    derive_custom_problem_source_evidence,
    parse_supported_function_signature,
    parse_supported_literal,
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

Constraints:
1 <= nums.length <= 10

Example 1:
Input: nums = [1, 2], target = 3
Output: 1
"""
    )

    assert evidence.to_payload() == {
        "statement": "Given nums and target, return the number of valid pairs.",
        "constraints": ["1 <= nums.length <= 10"],
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
        "parsed_visible_cases": [
            {
                "arguments": {"nums": [1, 2], "target": 3},
                "expected_output": 1,
                "input_text": "nums = [1, 2], target = 3",
                "output_text": "1",
                "explanation": "",
            }
        ],
    }
    assert contradicted_normalization_findings(
        [
            "MISSING_PROBLEM_TEXT",
            "MISSING_RETURN_BEHAVIOR",
            "MISSING_ARGUMENTS",
            "AMBIGUOUS_ARGUMENT_TYPES",
            "MISSING_EXAMPLE",
            "MISSING_CONSTRAINTS",
            "CONTRADICTORY_EXAMPLES",
        ],
        evidence,
    ) == (
        "MISSING_PROBLEM_TEXT",
        "MISSING_RETURN_BEHAVIOR",
        "MISSING_ARGUMENTS",
        "AMBIGUOUS_ARGUMENT_TYPES",
        "MISSING_EXAMPLE",
        "MISSING_CONSTRAINTS",
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
    assert evidence.parsed_visible_cases == ()
    assert contradicted_normalization_findings(["MISSING_EXAMPLE"], evidence) == ()


@pytest.mark.parametrize(
    ("source", "expected_arguments", "expected_output"),
    [
        (
            """int countPairs(vector<int> nums, int target)
Example 1:
Input: nums = [1, 2, 3, 4], target = 5
Output: 2
""",
            {"nums": [1, 2, 3, 4], "target": 5},
            2,
        ),
        (
            'bool accepts(string value)\nExample:\nInput: value = "ready"\nOutput: true\n',
            {"value": "ready"},
            True,
        ),
        (
            "int countWords(vector<string> words)\n"
            'Example:\nInput: words = ["a", "b"]\nOutput: 2\n',
            {"words": ["a", "b"]},
            2,
        ),
        (
            "bool hasZero(vector<vector<int>> grid)\n"
            "Example:\nInput: grid = [[1, 2], [0, 4]]\nOutput: True\n",
            {"grid": [[1, 2], [0, 4]]},
            True,
        ),
        (
            "string join(string left, string right)\nExample:\n"
            'Input: left = "a,b", right = "c"\nOutput: "a,b,c"\n',
            {"left": "a,b", "right": "c"},
            "a,b,c",
        ),
        (
            "bool toggle(bool enabled)\nExample:\nInput: enabled = true\nOutput: false\n",
            {"enabled": True},
            False,
        ),
        (
            "bool hasValue(vector<vector<string>> grid)\n"
            'Example:\nInput: grid = [["a,b"], ["c"]]\nOutput: true\n',
            {"grid": [["a,b"], ["c"]]},
            True,
        ),
    ],
)
def test_source_examples_parse_supported_typed_literals(
    source: str,
    expected_arguments: dict[str, object],
    expected_output: object,
) -> None:
    evidence = derive_custom_problem_source_evidence(source)

    assert len(evidence.parsed_visible_cases) == 1
    case = evidence.parsed_visible_cases[0]
    assert dict(case.arguments) == expected_arguments
    assert case.expected_output == expected_output


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("-12", -12),
        ("true", True),
        ('"hello"', "hello"),
        ("[1, 2]", [1, 2]),
        ('["a", "b"]', ["a", "b"]),
        ("[[1], [2, 3]]", [[1], [2, 3]]),
        ('[["a"], ["b,c"]]', [["a"], ["b,c"]]),
    ],
)
def test_literal_parser_covers_every_current_semantic_value_shape(
    literal: str,
    expected: object,
) -> None:
    assert parse_supported_literal(literal) == expected


@pytest.mark.parametrize(
    "literal",
    [
        "__import__('os').system('echo unsafe')",
        "[1, 2",
        '{"value": 1}',
        "1.5",
        "null",
        "[1,]",
        "[" * 10 + "1" + "]" * 10,
        "1" * 4_097,
    ],
)
def test_literal_parser_rejects_malformed_or_unsupported_input(literal: str) -> None:
    assert parse_supported_literal(literal) is None


def test_example_missing_output_produces_no_parsed_source_case() -> None:
    evidence = derive_custom_problem_source_evidence(
        "int solve(vector<int> nums)\nExample:\nInput: nums = [1, 2]\n"
    )

    assert evidence.parsed_visible_cases == ()


def test_example_arguments_must_exactly_match_the_source_signature() -> None:
    evidence = derive_custom_problem_source_evidence(
        "int solve(vector<int> nums, int target)\n"
        "Example:\nInput: values = [1, 2], target = 3\nOutput: 1\n"
    )

    assert evidence.signature is not None
    assert evidence.parsed_visible_cases == ()


def test_source_sections_preserve_statement_constraints_and_display_examples() -> None:
    evidence = derive_custom_problem_source_evidence(
        """Given values and a label, return the matching groups exactly as described.

FUNCTION SIGNATURE:
vector<vector<string>> collect(vector<string> values, string label)

CONSTRAINTS:
- 1 <= values.length <= 10
- values may contain quoted commas

EXAMPLE 1:
INPUT: values = ["a,b", "c"], label = "x,y"
OUTPUT: [["a,b"], ["c"]]
EXPLANATION: Preserve each nested group.

example 2:
input: values = ["z"], label = "plain"
output: [["z"]]
"""
    )

    assert evidence.statement == (
        "Given values and a label, return the matching groups exactly as described."
    )
    assert evidence.constraints == (
        "1 <= values.length <= 10",
        "values may contain quoted commas",
    )
    assert len(evidence.parsed_visible_cases) == 2
    first, second = evidence.parsed_visible_cases
    assert dict(first.arguments) == {"values": ["a,b", "c"], "label": "x,y"}
    assert first.expected_output == [["a,b"], ["c"]]
    assert first.input_text == 'values = ["a,b", "c"], label = "x,y"'
    assert first.output_text == '[["a,b"], ["c"]]'
    assert first.explanation == "Preserve each nested group."
    assert second.explanation == ""


def test_malformed_sections_do_not_create_partial_execution_authority() -> None:
    evidence = derive_custom_problem_source_evidence(
        """Return a result for the supplied values.
Function signature:
int solve(vector<int> values)
Constraints:
Example:
Input: values = [1, 2]
Output: not-an-int
Explanation: Input: values = [9]
"""
    )

    assert evidence.signature is not None
    assert evidence.constraints == ()
    assert evidence.parsed_visible_cases == ()
    assert contradicted_normalization_findings(["MISSING_EXAMPLE"], evidence) == ()

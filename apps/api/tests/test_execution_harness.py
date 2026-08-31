from __future__ import annotations

import json
from inspect import signature

import pytest

from app.execution.codecs import (
    ExecutionCodecError,
    compare_output,
    encode_value,
    validate_output,
)
from app.execution.harness import (
    MAX_CUSTOM_TEST_ARGUMENT_BYTES,
    CustomCaseSelection,
    CustomTestValidationError,
    UnsupportedExecutionSchema,
    harness_for_problem,
)
from app.execution.policy import (
    DEFAULT_COMPILE_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)
from app.execution.service import ExecutionService


@pytest.mark.parametrize(
    ("semantic_type", "value"),
    [
        ("int", -7),
        ("bool", True),
        ("string", "quote: \" and slash: \\"),
        ("int[]", []),
        ("string[]", ["", "alpha", "escaped \"value\""]),
        ("int[][]", [[1, -2], [], [3, 4]]),
        ("string[][]", [["a", "b"], [], [""]]),
    ],
)
def test_bounded_json_codec_round_trips_supported_semantic_types(
    semantic_type: str, value: object
) -> None:
    encoded = encode_value(value, semantic_type)
    assert json.loads(encoded) == value


@pytest.mark.parametrize("language", ("cpp", "python", "java"))
@pytest.mark.parametrize(
    ("semantic_type", "value"),
    [
        ("int", -7),
        ("bool", False),
        ("string", ""),
        ("int[]", []),
        ("string[]", ["a", "escaped \"value\""]),
        ("int[][]", [[1, -2], [], [3]]),
        ("string[][]", [["a"], [], ["b", "c"]]),
    ],
)
def test_generic_harness_supports_every_bounded_type(
    language: str, semantic_type: str, value: object
) -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": semantic_type}],
            "return_type": semantic_type,
            "comparator": "EXACT",
            "visible_cases": [
                {"arguments": {"value": value}, "expected_output": value}
            ],
            "custom_test_supported": True,
        }
    }
    harness, cases = harness_for_problem(schema, language)
    assert "echoValue" in harness
    assert cases[0].input_json == {"value": value}
    assert cases[0].expected_output == encode_value(value, semantic_type)
    assert cases[0].return_type == semantic_type


def test_exact_comparator_preserves_nested_array_order() -> None:
    compared = compare_output("[[2],[1]]", "[[1],[2]]", "int[][]", "EXACT")
    assert compared.status == "FAILED"
    assert compared.failure_classification == "VISIBLE_CASE_MISMATCH"


def test_unordered_list_ignores_only_top_level_order_and_preserves_multiplicity() -> None:
    assert compare_output("[2,1,1]", "[1,2,1]", "int[]", "UNORDERED_LIST").status == "PASSED"
    assert compare_output("[2,1]", "[1,2,1]", "int[]", "UNORDERED_LIST").status == "FAILED"
    assert (
        compare_output('[[2,1],[3]]', '[[3],[2,1]]', "int[][]", "UNORDERED_LIST").status
        == "PASSED"
    )
    assert (
        compare_output('[[1,2],[3]]', '[[3],[2,1]]', "int[][]", "UNORDERED_LIST").status
        == "FAILED"
    )


@pytest.mark.parametrize(
    ("actual", "classification"),
    [
        (None, "MISSING_CASE_OUTPUT"),
        ("not-json", "MALFORMED_EXECUTION_OUTPUT"),
        ("true", "INVALID_EXECUTION_OUTPUT_TYPE"),
    ],
)
def test_malformed_or_wrong_typed_sandbox_output_is_a_case_failure(
    actual: str | None, classification: str
) -> None:
    compared = compare_output(actual, "1", "int", "EXACT")
    assert compared.status == "FAILED"
    assert compared.failure_classification == classification


def test_custom_output_is_type_checked_without_a_correctness_comparison() -> None:
    executed = validate_output("42", "int")
    wrong_type = validate_output("true", "int")

    assert executed.status == "PASSED"
    assert executed.failure_classification is None
    assert wrong_type.status == "FAILED"
    assert wrong_type.failure_classification == "INVALID_EXECUTION_OUTPUT_TYPE"


def test_unsupported_semantic_type_rejects_before_harness_generation() -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "solve",
            "arguments": [{"name": "value", "type": "object"}],
            "return_type": "object",
            "visible_cases": [
                {"arguments": {"value": {}}, "expected_output": {}}
            ],
        }
    }
    with pytest.raises(UnsupportedExecutionSchema, match="supported execution schema"):
        harness_for_problem(schema, "cpp")
    with pytest.raises(ExecutionCodecError, match="Unsupported semantic type"):
        encode_value({}, "object")


def test_harness_is_driven_by_method_and_argument_schema_not_problem_identity() -> None:
    schema = {
        "catalog_order": 999,
        "title": "A title the engine never dispatches on",
        "execution": {
            "method_name": "configuredMethod",
            "arguments": [
                {"name": "words", "type": "string[]"},
                {"name": "enabled", "type": "bool"},
            ],
            "return_type": "string[]",
            "comparator": "UNORDERED_LIST",
            "visible_cases": [
                {
                    "arguments": {"words": ["b", "a"], "enabled": True},
                    "expected_output": ["a", "b"],
                }
            ],
        },
    }
    for language in ("cpp", "python", "java"):
        harness, cases = harness_for_problem(schema, language)
        assert "configuredMethod" in harness
        assert cases[0].comparator == "UNORDERED_LIST"


def test_python_testcase_json_is_inserted_after_trusted_placeholder_metadata() -> None:
    values = [
        "__METHOD__",
        "__RETURN_TYPE__",
        "__CASES_JSON__",
        'quoted "value"',
        "slash\\value",
        "a\nb",
    ]
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": "string[]"}],
            "return_type": "string[]",
            "comparator": "EXACT",
            "visible_cases": [
                {"arguments": {"value": values}, "expected_output": values}
            ],
        }
    }
    harness, _ = harness_for_problem(schema, "python")
    encoded_cases = json.dumps([[values]], ensure_ascii=False, separators=(",", ":"))

    assert repr(encoded_cases) in harness
    assert all(value in harness for value in values[:3])


def test_execution_service_defaults_use_the_canonical_candidate_policy() -> None:
    parameters = signature(ExecutionService.__init__).parameters
    assert parameters["compile_timeout_seconds"].default == DEFAULT_COMPILE_TIMEOUT_SECONDS
    assert parameters["run_timeout_seconds"].default == DEFAULT_RUN_TIMEOUT_SECONDS
    assert parameters["memory_limit_mb"].default == DEFAULT_MEMORY_LIMIT_MB
    assert parameters["output_limit_bytes"].default == DEFAULT_OUTPUT_LIMIT_BYTES


@pytest.mark.parametrize(
    ("semantic_type", "value"),
    [
        ("int", -7),
        ("bool", True),
        ("string", ""),
        ("string", 'quote: " slash: \\ newline:\n'),
        ("int[]", []),
        ("int[]", [1, -2, 3]),
        ("string[]", ["", "alpha", 'escaped "value"']),
        ("int[][]", [[1, -2], [], [3, 4]]),
        ("string[][]", [["a", "b"], [], [""]]),
    ],
)
def test_custom_case_accepts_every_bounded_semantic_shape(
    semantic_type: str, value: object
) -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": semantic_type}],
            "return_type": semantic_type,
            "visible_cases": [{"arguments": {"value": value}, "expected_output": value}],
            "custom_test_supported": True,
        }
    }

    for language in ("cpp", "python", "java"):
        harness, cases = harness_for_problem(
            schema,
            language,
            case_selection=CustomCaseSelection({"value": value}),
        )
        assert "echoValue" in harness
        assert len(cases) == 1
        assert cases[0].identifier == "custom-1"
        assert cases[0].input_json == {"value": value}
        assert cases[0].expected_output is None


def test_custom_case_accepts_multiple_named_arguments() -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "solve",
            "arguments": [
                {"name": "nums", "type": "int[]"},
                {"name": "target", "type": "int"},
            ],
            "return_type": "int[]",
            "visible_cases": [
                {"arguments": {"nums": [2, 7], "target": 9}, "expected_output": [0, 1]}
            ],
        }
    }
    _, cases = harness_for_problem(
        schema,
        "python",
        case_selection=CustomCaseSelection({"target": 4, "nums": [1, 3]}),
    )
    assert cases[0].input_json == {"target": 4, "nums": [1, 3]}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "missing arguments: value"),
        ({"value": 1, "extra": 2}, "unexpected arguments: extra"),
        ({"value": "1"}, "does not match semantic type int"),
        ({"value": True}, "does not match semantic type int"),
    ],
)
def test_invalid_custom_arguments_are_rejected_before_harness_generation(
    arguments: dict[str, object], message: str
) -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": "int"}],
            "return_type": "int",
            "visible_cases": [{"arguments": {"value": 1}, "expected_output": 1}],
        }
    }
    with pytest.raises(CustomTestValidationError, match=message):
        harness_for_problem(
            schema,
            "cpp",
            case_selection=CustomCaseSelection(arguments),
        )


def test_custom_case_enforces_canonical_serialized_byte_bound() -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": "string"}],
            "return_type": "string",
            "visible_cases": [{"arguments": {"value": "ok"}, "expected_output": "ok"}],
        }
    }
    oversized = "x" * MAX_CUSTOM_TEST_ARGUMENT_BYTES
    with pytest.raises(CustomTestValidationError, match="byte limit"):
        harness_for_problem(
            schema,
            "python",
            case_selection=CustomCaseSelection({"value": oversized}),
        )


def test_exact_problem_version_can_disable_custom_tests() -> None:
    schema: dict[str, object] = {
        "execution": {
            "method_name": "echoValue",
            "arguments": [{"name": "value", "type": "int"}],
            "return_type": "int",
            "visible_cases": [{"arguments": {"value": 1}, "expected_output": 1}],
            "custom_test_supported": False,
        }
    }
    with pytest.raises(CustomTestValidationError, match="not supported"):
        harness_for_problem(
            schema,
            "java",
            case_selection=CustomCaseSelection({"value": 2}),
        )

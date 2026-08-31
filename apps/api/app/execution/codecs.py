"""Bounded JSON execution protocol and deterministic result comparators."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from app.problems.content import validate_semantic_value

SUPPORTED_SEMANTIC_TYPES = frozenset(
    {"int", "bool", "string", "int[]", "string[]", "int[][]", "string[][]"}
)
SUPPORTED_COMPARATORS = frozenset({"EXACT", "UNORDERED_LIST"})


class ExecutionCodecError(ValueError):
    """A value cannot cross the bounded execution protocol safely."""


@dataclass(frozen=True)
class ComparedOutput:
    actual_output: str | None
    status: str
    failure_classification: str | None


def encode_value(value: object, semantic_type: str) -> str:
    _require_supported_type(semantic_type)
    if not validate_semantic_value(value, semantic_type):
        raise ExecutionCodecError(f"Value does not match semantic type {semantic_type}")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compare_output(
    actual_output: str | None,
    expected_output: str,
    semantic_type: str,
    comparator: str,
) -> ComparedOutput:
    _require_supported_type(semantic_type)
    if comparator not in SUPPORTED_COMPARATORS:
        raise ExecutionCodecError(f"Unsupported comparator {comparator}")
    if actual_output is None:
        return ComparedOutput(None, "FAILED", "MISSING_CASE_OUTPUT")
    try:
        actual = json.loads(actual_output)
        expected = json.loads(expected_output)
    except (json.JSONDecodeError, TypeError):
        return ComparedOutput(actual_output, "FAILED", "MALFORMED_EXECUTION_OUTPUT")
    if not validate_semantic_value(actual, semantic_type):
        return ComparedOutput(actual_output, "FAILED", "INVALID_EXECUTION_OUTPUT_TYPE")
    if not validate_semantic_value(expected, semantic_type):
        raise ExecutionCodecError("Trusted expected output does not match its semantic type")
    if comparator == "EXACT":
        matches = actual == expected
    else:
        if not semantic_type.endswith("[]"):
            raise ExecutionCodecError("UNORDERED_LIST requires an array return type")
        matches = Counter(_element_identity(item) for item in actual) == Counter(
            _element_identity(item) for item in expected
        )
    return ComparedOutput(
        actual_output,
        "PASSED" if matches else "FAILED",
        None if matches else "VISIBLE_CASE_MISMATCH",
    )


def _element_identity(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_supported_type(semantic_type: str) -> None:
    if semantic_type not in SUPPORTED_SEMANTIC_TYPES:
        raise ExecutionCodecError(f"Unsupported semantic type {semantic_type}")

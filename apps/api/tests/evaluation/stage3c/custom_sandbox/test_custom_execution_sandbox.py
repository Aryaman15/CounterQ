"""Real isolated-sandbox acceptance for candidate-created custom cases."""

from __future__ import annotations

import json
import os

import pytest

from app.execution.harness import CustomCaseSelection, execution_request_for_problem
from app.execution.policy import (
    DEFAULT_COMPILE_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)
from app.execution.provider import ExecutionRequest
from app.execution.sandbox_provider import LocalSandboxExecutorProvider

pytestmark = pytest.mark.skipif(
    os.getenv("COUNTERQ_SANDBOX_EVALUATION") != "1",
    reason="requires the local isolated execution sandbox",
)

SANDBOX_URL = "http://127.0.0.1:8010"


def _custom_request(
    *,
    language: str,
    source_code: str,
    arguments: list[dict[str, str]],
    return_type: str,
    custom_arguments: dict[str, object],
) -> ExecutionRequest:
    placeholder_arguments = {
        argument["name"]: _placeholder(argument["type"]) for argument in arguments
    }
    return execution_request_for_problem(
        io_schema={
            "execution": {
                "method_name": "solve",
                "arguments": arguments,
                "return_type": return_type,
                "visible_cases": [
                    {
                        "arguments": placeholder_arguments,
                        "expected_output": _placeholder(return_type),
                    }
                ],
                "custom_test_supported": True,
            }
        },
        language=language,
        source_code=source_code,
        compile_timeout_seconds=DEFAULT_COMPILE_TIMEOUT_SECONDS,
        run_timeout_seconds=DEFAULT_RUN_TIMEOUT_SECONDS,
        memory_limit_mb=DEFAULT_MEMORY_LIMIT_MB,
        output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
        case_selection=CustomCaseSelection(custom_arguments),
    )


def _placeholder(semantic_type: str) -> object:
    if semantic_type == "int":
        return 0
    if semantic_type == "bool":
        return False
    if semantic_type == "string":
        return ""
    return []


SCENARIOS = [
    pytest.param(
        "cpp",
        (
            "class Solution { public: int solve(vector<int> nums, int offset) { "
            "int total = offset + 1; for (int value : nums) total += value; return total; } };"
        ),
        [{"name": "nums", "type": "int[]"}, {"name": "offset", "type": "int"}],
        "int",
        {"nums": [-2, 5], "offset": 2},
        6,
        id="cpp-scalar-multiple-arguments-no-verdict",
    ),
    pytest.param(
        "python",
        (
            "class Solution:\n"
            "    def solve(self, words, suffix):\n"
            "        return [word + suffix for word in words]"
        ),
        [
            {"name": "words", "type": "string[]"},
            {"name": "suffix", "type": "string"},
        ],
        "string[]",
        {"words": ['a"b', "slash\\value", ""], "suffix": "\n!"},
        ['a"b\n!', "slash\\value\n!", "\n!"],
        id="python-array-result-string-escaping",
    ),
    pytest.param(
        "java",
        (
            "class Solution { public int[] solve(int[][] rows) { "
            "int[] result = new int[rows.length]; "
            "for (int i = 0; i < rows.length; i++) for (int value : rows[i]) result[i] += value; "
            "return result; } }"
        ),
        [{"name": "rows", "type": "int[][]"}],
        "int[]",
        {"rows": [[1, -2], [], [3, 4]]},
        [-1, 0, 7],
        id="java-nested-array-argument-array-result",
    ),
]


@pytest.mark.parametrize(
    ("language", "source", "arguments", "return_type", "custom_arguments", "expected_actual"),
    SCENARIOS,
)
async def test_custom_cases_use_generic_harness_without_correctness_verdict(
    language: str,
    source: str,
    arguments: list[dict[str, str]],
    return_type: str,
    custom_arguments: dict[str, object],
    expected_actual: object,
) -> None:
    request = _custom_request(
        language=language,
        source_code=source,
        arguments=arguments,
        return_type=return_type,
        custom_arguments=custom_arguments,
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)

    assert outcome.status == "SUCCEEDED"
    assert len(request.cases) == len(outcome.cases) == 1
    assert request.cases[0].identifier == "custom-1"
    assert request.cases[0].expected_output is None
    assert outcome.cases[0].status == "PASSED"
    assert outcome.cases[0].failure_classification is None
    assert json.loads(outcome.cases[0].actual_output or "null") == expected_actual


@pytest.mark.parametrize(
    ("language", "source"),
    [
        (
            "cpp",
            (
                "class Solution { int calls = 0; public: int solve(int value) "
                "{ (void)value; return ++calls; } };"
            ),
        ),
        (
            "python",
            (
                "class Solution:\n"
                "    def __init__(self): self.calls = 0\n"
                "    def solve(self, value): self.calls += 1; return self.calls"
            ),
        ),
        (
            "java",
            (
                "class Solution { private int calls = 0; "
                "public int solve(int value) { return ++calls; } }"
            ),
        ),
    ],
)
async def test_one_custom_invocation_receives_a_fresh_solution(
    language: str, source: str
) -> None:
    request = _custom_request(
        language=language,
        source_code=source,
        arguments=[{"name": "value", "type": "int"}],
        return_type="int",
        custom_arguments={"value": 99},
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    assert outcome.status == "SUCCEEDED"
    assert json.loads(outcome.cases[0].actual_output or "null") == 1

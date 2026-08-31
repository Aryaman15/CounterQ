"""Full curated reference-solution execution through the candidate engine."""

from __future__ import annotations

import os

import pytest

from app.execution.harness import execution_request_for_problem
from app.execution.sandbox_provider import LocalSandboxExecutorProvider
from app.problems.content import CuratedContent, ReferenceSolution, load_curated_content

pytestmark = pytest.mark.skipif(
    os.getenv("COUNTERQ_SANDBOX_EVALUATION") != "1",
    reason="requires the local isolated execution sandbox",
)

SANDBOX_URL = "http://127.0.0.1:8010"


def _primary_reference(entry: CuratedContent, language: str) -> ReferenceSolution:
    primary_approach = entry.interview_pack.expected_approaches[0].approach_id
    return next(
        solution
        for solution in entry.interview_pack.reference_solutions
        if solution.approach_id == primary_approach and solution.language == language
    )


CATALOG_CASES = [
    pytest.param(
        entry,
        language,
        _primary_reference(entry, language),
        id=(
            f"{entry.problem.slug}@{entry.problem.version}-"
            f"pack-{entry.interview_pack.version}-{language}"
        ),
    )
    for entry in load_curated_content()
    for language in ("cpp", "python", "java")
]


@pytest.mark.parametrize(("entry", "language", "reference"), CATALOG_CASES)
async def test_primary_reference_solution_passes_every_visible_case(
    entry: CuratedContent,
    language: str,
    reference: ReferenceSolution,
) -> None:
    request = execution_request_for_problem(
        io_schema={"execution": entry.problem.execution.model_dump(mode="json")},
        language=language,
        source_code=reference.source_code,
        compile_timeout_seconds=8,
        run_timeout_seconds=3,
        memory_limit_mb=384,
        output_limit_bytes=65536,
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    context = (
        f"slug={entry.problem.slug} ProblemVersion={entry.problem.version} "
        f"InterviewPackVersion={entry.interview_pack.version} language={language} "
        f"execution_status={outcome.status}"
    )
    assert outcome.status == "SUCCEEDED", context
    failures = [
        (
            index,
            case.identifier,
            case.status,
            case.failure_classification,
        )
        for index, case in enumerate(outcome.cases, start=1)
        if case.status != "PASSED"
    ]
    assert len(outcome.cases) == len(entry.problem.execution.visible_cases), (
        f"{context} returned_cases={len(outcome.cases)} "
        f"expected_cases={len(entry.problem.execution.visible_cases)}"
    )
    assert not failures, f"{context} case_failures={failures}"


SEMANTIC_VALUES: list[tuple[str, object]] = [
    ("int", -7),
    ("bool", True),
    ("string", "escaped \"value\" with \\ and newline\n"),
    ("int[]", []),
    ("string[]", ["", "escaped \"value\"", "slash\\value"]),
    ("int[][]", [[1, -2], [], [3, 4]]),
    ("string[][]", [["", "quoted \"value\""], [], ["tail"]]),
]

CPP_TYPES = {
    "int": "int",
    "bool": "bool",
    "string": "string",
    "int[]": "vector<int>",
    "string[]": "vector<string>",
    "int[][]": "vector<vector<int>>",
    "string[][]": "vector<vector<string>>",
}
JAVA_TYPES = {
    "int": "int",
    "bool": "boolean",
    "string": "String",
    "int[]": "int[]",
    "string[]": "String[]",
    "int[][]": "int[][]",
    "string[][]": "String[][]",
}


def _echo_source(language: str, semantic_type: str) -> str:
    if language == "cpp":
        value_type = CPP_TYPES[semantic_type]
        return (
            f"class Solution {{ public: {value_type} echoValue({value_type} value) "
            "{ return value; } };"
        )
    if language == "python":
        return "class Solution:\n    def echoValue(self, value):\n        return value"
    value_type = JAVA_TYPES[semantic_type]
    return (
        f"class Solution {{ public {value_type} echoValue({value_type} value) "
        "{ return value; } }"
    )


@pytest.mark.parametrize("language", ("cpp", "python", "java"))
@pytest.mark.parametrize(("semantic_type", "value"), SEMANTIC_VALUES)
async def test_bounded_semantic_type_round_trips_through_real_sandbox(
    language: str,
    semantic_type: str,
    value: object,
) -> None:
    request = execution_request_for_problem(
        io_schema={
            "execution": {
                "method_name": "echoValue",
                "arguments": [{"name": "value", "type": semantic_type}],
                "return_type": semantic_type,
                "comparator": "EXACT",
                "visible_cases": [
                    {"arguments": {"value": value}, "expected_output": value}
                ],
            }
        },
        language=language,
        source_code=_echo_source(language, semantic_type),
        compile_timeout_seconds=8,
        run_timeout_seconds=3,
        memory_limit_mb=384,
        output_limit_bytes=65536,
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    context = f"semantic_type={semantic_type} language={language} status={outcome.status}"
    assert outcome.status == "SUCCEEDED", context
    assert len(outcome.cases) == 1, context
    assert outcome.cases[0].status == "PASSED", (
        f"{context} failure={outcome.cases[0].failure_classification}"
    )

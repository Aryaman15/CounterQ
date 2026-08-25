"""Trusted, versioned harnesses derived from reviewed ProblemVersion I/O schemas."""

from __future__ import annotations

import json

from app.execution.provider import ExecutionCase


class UnsupportedExecutionSchema(ValueError):
    pass


def cpp_harness_for_problem(io_schema: dict[str, object]) -> tuple[str, tuple[ExecutionCase, ...]]:
    execution = io_schema.get("execution")
    if not isinstance(execution, dict) or execution.get("harness") != "longest_substring_v1":
        raise UnsupportedExecutionSchema("Problem version has no supported C++ execution harness")
    raw_cases = execution.get("visible_cases")
    if not isinstance(raw_cases, list):
        raise UnsupportedExecutionSchema("Problem version has no visible execution cases")
    cases: list[ExecutionCase] = []
    initializer: list[str] = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise UnsupportedExecutionSchema("Visible case is malformed")
        value = raw_case.get("s")
        expected = raw_case.get("expected")
        if not isinstance(value, str) or not isinstance(expected, int):
            raise UnsupportedExecutionSchema("Visible case has unsupported input/output")
        cases.append(
            ExecutionCase(
                identifier=f"visible-{index}",
                input_json={"s": value},
                expected_output=str(expected),
            )
        )
        initializer.append("{" + json.dumps(value) + ", " + str(expected) + "}")
    harness = """
int main() {
    Solution solution;
    vector<pair<string, int>> cases = {__CASES__};
    for (size_t index = 0; index < cases.size(); ++index) {
        int actual = solution.lengthOfLongestSubstring(cases[index].first);
        cout << "COUNTERQ_CASE\\t" << index + 1 << "\\t" << actual << "\\n";
    }
    return 0;
}
""".replace("__CASES__", ", ".join(initializer))
    return harness, tuple(cases)

"""Trusted, versioned harnesses derived from reviewed ProblemVersion I/O schemas."""

from __future__ import annotations

import json

from app.execution.provider import ExecutionCase


class UnsupportedExecutionSchema(ValueError):
    pass


def harness_for_problem(
    io_schema: dict[str, object], language: str
) -> tuple[str, tuple[ExecutionCase, ...]]:
    execution = io_schema.get("execution")
    if not isinstance(execution, dict) or execution.get("harness") != "longest_substring_v1":
        raise UnsupportedExecutionSchema("Problem version has no supported execution harness")
    raw_cases = execution.get("visible_cases")
    if not isinstance(raw_cases, list):
        raise UnsupportedExecutionSchema("Problem version has no visible execution cases")
    cases: list[ExecutionCase] = []
    values: list[str] = []
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
        values.append(json.dumps(value))
    return _harness(language, values), tuple(cases)


def _harness(language: str, values: list[str]) -> str:
    if language == "cpp":
        return """
int main() {
    Solution solution;
    vector<string> cases = {__CASES__};
    for (size_t index = 0; index < cases.size(); ++index)
        int actual = solution.lengthOfLongestSubstring(cases[index]);
        cout << "COUNTERQ_CASE\\t" << index + 1 << "\\t" << actual << "\\n";
}
""".replace("__CASES__", ", ".join(values))
    if language == "python":
        return """
solution = Solution()
for index, value in enumerate([__CASES__], start=1):
    print(f"COUNTERQ_CASE\\t{index}\\t{solution.lengthOfLongestSubstring(value)}")
""".replace("__CASES__", ", ".join(values))
    if language == "java":
        return """
public class Main {
    public static void main(String[] args) {
        Solution solution = new Solution();
        String[] cases = {__CASES__};
        for (int index = 0; index < cases.length; index++)
            int actual = solution.lengthOfLongestSubstring(cases[index]);
            System.out.println("COUNTERQ_CASE\\t" + (index + 1) + "\\t" + actual);
    }
}
""".replace("__CASES__", ", ".join(values))
    raise UnsupportedExecutionSchema("Unsupported configured execution language")

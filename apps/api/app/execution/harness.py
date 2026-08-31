"""Data-driven trusted harnesses for immutable curated ProblemVersions."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.execution.codecs import ExecutionCodecError, encode_value
from app.execution.provider import ExecutionCase, ExecutionRequest
from app.problems.content import ExecutionDefinition


class UnsupportedExecutionSchema(ValueError):
    pass


def harness_for_problem(
    io_schema: dict[str, object], language: str
) -> tuple[str, tuple[ExecutionCase, ...]]:
    execution = _execution_definition(io_schema)
    cases = tuple(
        ExecutionCase(
            identifier=f"visible-{index}",
            input_json=dict(case.arguments),
            expected_output=encode_value(case.expected_output, execution.return_type),
            return_type=execution.return_type,
            comparator=execution.comparator,
        )
        for index, case in enumerate(execution.visible_cases, start=1)
    )
    if language == "cpp":
        return _cpp_harness(execution), cases
    if language == "python":
        return _python_harness(execution), cases
    if language == "java":
        return _java_harness(execution), cases
    raise UnsupportedExecutionSchema("Unsupported configured execution language")


def execution_request_for_problem(
    *,
    io_schema: dict[str, object],
    language: str,
    source_code: str,
    compile_timeout_seconds: int,
    run_timeout_seconds: int,
    memory_limit_mb: int,
    output_limit_bytes: int,
) -> ExecutionRequest:
    harness, cases = harness_for_problem(io_schema, language)
    return ExecutionRequest(
        language=language,
        source_code=source_code,
        harness=harness,
        cases=cases,
        compile_timeout_seconds=compile_timeout_seconds,
        run_timeout_seconds=run_timeout_seconds,
        memory_limit_mb=memory_limit_mb,
        output_limit_bytes=output_limit_bytes,
    )


def _execution_definition(io_schema: dict[str, object]) -> ExecutionDefinition:
    raw = io_schema.get("execution")
    try:
        execution = ExecutionDefinition.model_validate(raw)
    except ValidationError as exc:
        raise UnsupportedExecutionSchema(
            "Problem version has no supported execution schema"
        ) from exc
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", execution.method_name) is None:
        raise UnsupportedExecutionSchema("Execution method name is not a safe identifier")
    if execution.comparator == "UNORDERED_LIST" and not execution.return_type.endswith("[]"):
        raise UnsupportedExecutionSchema("UNORDERED_LIST requires an array return type")
    try:
        for case in execution.visible_cases:
            encode_value(case.expected_output, execution.return_type)
    except ExecutionCodecError as exc:
        raise UnsupportedExecutionSchema(str(exc)) from exc
    return execution


def _cpp_harness(execution: ExecutionDefinition) -> str:
    invocations = []
    for index, case in enumerate(execution.visible_cases, start=1):
        arguments = ", ".join(
            _cpp_literal(case.arguments[argument.name], argument.type)
            for argument in execution.arguments
        )
        invocations.append(
            "    {\n"
            "        Solution solution;\n"
            f'        cout << "COUNTERQ_CASE\\t{index}\\t" << '
            f'counterq_json(solution.{execution.method_name}({arguments})) << "\\n";\n'
            "    }"
        )
    return r"""
static string counterq_json_escape(const string& value) {
    string out = "\"";
    const char hex[] = "0123456789abcdef";
    for (unsigned char ch : value) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (ch < 0x20) {
                    out += "\\u00";
                    out += hex[ch >> 4];
                    out += hex[ch & 0x0f];
                } else {
                    out += static_cast<char>(ch);
                }
        }
    }
    return out + "\"";
}

static string counterq_json(int value) { return to_string(value); }
static string counterq_json(bool value) { return value ? "true" : "false"; }
static string counterq_json(const string& value) { return counterq_json_escape(value); }

template <typename T>
static string counterq_json(const vector<T>& values) {
    string out = "[";
    for (size_t index = 0; index < values.size(); ++index) {
        if (index != 0) out += ",";
        out += counterq_json(values[index]);
    }
    return out + "]";
}

int main() {
__INVOCATIONS__
}
""".replace("__INVOCATIONS__", "\n".join(invocations))


def _python_harness(execution: ExecutionDefinition) -> str:
    cases = [
        [case.arguments[argument.name] for argument in execution.arguments]
        for case in execution.visible_cases
    ]
    encoded_cases = json.dumps(cases, ensure_ascii=False, separators=(",", ":"))
    template = """
import json as _counterq_json_module

def _counterq_valid(value, semantic_type):
    if semantic_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if semantic_type == "bool":
        return isinstance(value, bool)
    if semantic_type == "string":
        return isinstance(value, str)
    if semantic_type.endswith("[]"):
        inner = semantic_type[:-2]
        return isinstance(value, list) and all(_counterq_valid(item, inner) for item in value)
    return False

_counterq_cases = _counterq_json_module.loads(__CASES_JSON__)
for _counterq_index, _counterq_arguments in enumerate(_counterq_cases, start=1):
    _counterq_solution = Solution()
    _counterq_actual = _counterq_solution.__METHOD__(*_counterq_arguments)
    if not _counterq_valid(_counterq_actual, __RETURN_TYPE__):
        raise TypeError("candidate result does not match the configured return type")
    _counterq_encoded = _counterq_json_module.dumps(
        _counterq_actual, ensure_ascii=False, separators=(",", ":")
    )
    print(f"COUNTERQ_CASE\\t{_counterq_index}\\t{_counterq_encoded}")
"""
    trusted_metadata = template.replace("__METHOD__", execution.method_name).replace(
        "__RETURN_TYPE__", json.dumps(execution.return_type)
    )
    return trusted_metadata.replace("__CASES_JSON__", repr(encoded_cases))


def _java_harness(execution: ExecutionDefinition) -> str:
    invocations = []
    for index, case in enumerate(execution.visible_cases, start=1):
        arguments = ", ".join(
            _java_literal(case.arguments[argument.name], argument.type)
            for argument in execution.arguments
        )
        invocations.append(
            "        {\n"
            "            Solution solution = new Solution();\n"
            f'            System.out.println("COUNTERQ_CASE\\t{index}\\t" + '
            f'counterqJson(solution.{execution.method_name}({arguments})));\n'
            "        }"
        )
    return r"""
public class Main {
    private static String counterqJson(int value) { return Integer.toString(value); }
    private static String counterqJson(boolean value) { return value ? "true" : "false"; }
    private static String counterqJson(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (ch < 0x20) out.append(String.format("\\u%04x", (int) ch));
                    else out.append(ch);
                }
            }
        }
        return out.append('\"').toString();
    }
    private static String counterqJson(int[] values) {
        StringBuilder out = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index != 0) out.append(',');
            out.append(counterqJson(values[index]));
        }
        return out.append(']').toString();
    }
    private static String counterqJson(String[] values) {
        StringBuilder out = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index != 0) out.append(',');
            out.append(counterqJson(values[index]));
        }
        return out.append(']').toString();
    }
    private static String counterqJson(int[][] values) {
        StringBuilder out = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index != 0) out.append(',');
            out.append(counterqJson(values[index]));
        }
        return out.append(']').toString();
    }
    private static String counterqJson(String[][] values) {
        StringBuilder out = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index != 0) out.append(',');
            out.append(counterqJson(values[index]));
        }
        return out.append(']').toString();
    }

    public static void main(String[] args) {
__INVOCATIONS__
    }
}
""".replace("__INVOCATIONS__", "\n".join(invocations))


def _cpp_literal(value: object, semantic_type: str) -> str:
    if semantic_type == "int":
        return str(value)
    if semantic_type == "bool":
        return "true" if value else "false"
    if semantic_type == "string":
        return json.dumps(value, ensure_ascii=False)
    if semantic_type.endswith("[]"):
        assert isinstance(value, list)
        inner = semantic_type[:-2]
        return "{" + ", ".join(_cpp_literal(item, inner) for item in value) + "}"
    raise UnsupportedExecutionSchema(f"Unsupported semantic type {semantic_type}")


def _java_literal(value: object, semantic_type: str) -> str:
    if semantic_type == "int":
        return str(value)
    if semantic_type == "bool":
        return "true" if value else "false"
    if semantic_type == "string":
        return json.dumps(value, ensure_ascii=False)
    if semantic_type.endswith("[]"):
        assert isinstance(value, list)
        inner = semantic_type[:-2]
        java_type = {
            "int[]": "int[]",
            "string[]": "String[]",
            "int[][]": "int[][]",
            "string[][]": "String[][]",
        }.get(semantic_type)
        if java_type is None:
            raise UnsupportedExecutionSchema(f"Unsupported semantic type {semantic_type}")
        return (
            f"new {java_type}{{"
            + ", ".join(_java_literal(item, inner) for item in value)
            + "}"
        )
    raise UnsupportedExecutionSchema(f"Unsupported semantic type {semantic_type}")

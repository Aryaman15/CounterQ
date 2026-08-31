"""Data-driven trusted harnesses for immutable curated ProblemVersions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.execution.codecs import ExecutionCodecError, encode_value
from app.execution.provider import ExecutionCase, ExecutionRequest
from app.problems.content import ExecutionDefinition, validate_semantic_value

MAX_CUSTOM_TEST_ARGUMENT_BYTES = 16_384


class UnsupportedExecutionSchema(ValueError):
    pass


class CustomTestValidationError(ValueError):
    """Candidate-safe deterministic rejection of an invalid custom testcase."""


@dataclass(frozen=True)
class VisibleCaseSelection:
    pass


@dataclass(frozen=True)
class CustomCaseSelection:
    arguments: dict[str, object]


type CaseSelection = VisibleCaseSelection | CustomCaseSelection


def harness_for_problem(
    io_schema: dict[str, object],
    language: str,
    *,
    case_selection: CaseSelection | None = None,
) -> tuple[str, tuple[ExecutionCase, ...]]:
    execution = _execution_definition(io_schema)
    cases = _selected_cases(execution, case_selection or VisibleCaseSelection())
    if language == "cpp":
        return _cpp_harness(execution, cases), cases
    if language == "python":
        return _python_harness(execution, cases), cases
    if language == "java":
        return _java_harness(execution, cases), cases
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
    case_selection: CaseSelection | None = None,
) -> ExecutionRequest:
    harness, cases = harness_for_problem(io_schema, language, case_selection=case_selection)
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


def _selected_cases(
    execution: ExecutionDefinition, selection: CaseSelection
) -> tuple[ExecutionCase, ...]:
    if isinstance(selection, VisibleCaseSelection):
        return tuple(
            ExecutionCase(
                identifier=f"visible-{index}",
                input_json=dict(case.arguments),
                expected_output=encode_value(case.expected_output, execution.return_type),
                return_type=execution.return_type,
                comparator=execution.comparator,
            )
            for index, case in enumerate(execution.visible_cases, start=1)
        )
    if not execution.custom_test_supported:
        raise CustomTestValidationError("Custom tests are not supported for this problem version")
    arguments = dict(selection.arguments)
    configured = {argument.name: argument.type for argument in execution.arguments}
    if set(arguments) != set(configured):
        missing = sorted(set(configured) - set(arguments))
        extra = sorted(set(arguments) - set(configured))
        details = []
        if missing:
            details.append(f"missing arguments: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected arguments: {', '.join(extra)}")
        raise CustomTestValidationError("Custom arguments do not match: " + "; ".join(details))
    for name, semantic_type in configured.items():
        if not validate_semantic_value(arguments[name], semantic_type):
            raise CustomTestValidationError(
                f"Custom argument {name} does not match semantic type {semantic_type}"
            )
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CustomTestValidationError("Custom arguments are not valid structured JSON") from exc
    if len(encoded) > MAX_CUSTOM_TEST_ARGUMENT_BYTES:
        raise CustomTestValidationError(
            f"Custom arguments exceed the {MAX_CUSTOM_TEST_ARGUMENT_BYTES}-byte limit"
        )
    return (
        ExecutionCase(
            identifier="custom-1",
            input_json=arguments,
            expected_output=None,
            return_type=execution.return_type,
            comparator=execution.comparator,
        ),
    )


def _cpp_harness(execution: ExecutionDefinition, cases: tuple[ExecutionCase, ...]) -> str:
    invocations = []
    for case in cases:
        arguments = ", ".join(
            _cpp_literal(case.input_json[argument.name], argument.type)
            for argument in execution.arguments
        )
        invocations.append(
            "    {\n"
            "        Solution solution;\n"
            "        counterq_emit("
            f"counterq_json(solution.{execution.method_name}({arguments})));\n"
            "    }"
        )
    return r"""
#include <cerrno>
#include <unistd.h>

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

static void counterq_emit(const string& value) {
    const char* descriptor_text = getenv("COUNTERQ_RESULT_FD");
    if (descriptor_text == nullptr) _Exit(70);
    const int descriptor = static_cast<int>(strtol(descriptor_text, nullptr, 10));
    const string frame = value + "\n";
    size_t offset = 0;
    while (offset < frame.size()) {
        const ssize_t written = ::write(
            descriptor, frame.data() + offset, frame.size() - offset
        );
        if (written > 0) {
            offset += static_cast<size_t>(written);
        } else if (written < 0 && errno == EINTR) {
            continue;
        } else {
            _Exit(70);
        }
    }
}

int main() {
__INVOCATIONS__
}
""".replace("__INVOCATIONS__", "\n".join(invocations))


def _python_harness(execution: ExecutionDefinition, cases: tuple[ExecutionCase, ...]) -> str:
    case_values = [
        [case.input_json[argument.name] for argument in execution.arguments] for case in cases
    ]
    encoded_cases = json.dumps(case_values, ensure_ascii=False, separators=(",", ":"))
    template = """
import json as _counterq_json_module
import os as _counterq_os_module

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
    _counterq_frame = (_counterq_encoded + "\\n").encode("utf-8")
    _counterq_offset = 0
    _counterq_result_fd = int(_counterq_os_module.environ["COUNTERQ_RESULT_FD"])
    while _counterq_offset < len(_counterq_frame):
        _counterq_written = _counterq_os_module.write(
            _counterq_result_fd, _counterq_frame[_counterq_offset:]
        )
        if _counterq_written <= 0:
            raise RuntimeError("trusted result channel closed")
        _counterq_offset += _counterq_written
"""
    trusted_metadata = template.replace("__METHOD__", execution.method_name).replace(
        "__RETURN_TYPE__", json.dumps(execution.return_type)
    )
    return trusted_metadata.replace("__CASES_JSON__", repr(encoded_cases))


def _java_harness(execution: ExecutionDefinition, cases: tuple[ExecutionCase, ...]) -> str:
    invocations = []
    for case in cases:
        arguments = ", ".join(
            _java_literal(case.input_json[argument.name], argument.type)
            for argument in execution.arguments
        )
        invocations.append(
            "        {\n"
            "            Solution solution = new Solution();\n"
            "            counterqEmit("
            f"counterqJson(solution.{execution.method_name}({arguments})));\n"
            "        }"
        )
    return r"""
import java.io.FileOutputStream;
import java.io.FileDescriptor;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;

public class Main {
    private static final FileOutputStream COUNTERQ_RESULT_OUTPUT = counterqResultOutput();

    private static FileOutputStream counterqResultOutput() {
        String descriptor = System.getenv("COUNTERQ_RESULT_FD");
        if (descriptor == null) Runtime.getRuntime().halt(70);
        try {
            FileDescriptor fileDescriptor = new FileDescriptor();
            Field field = FileDescriptor.class.getDeclaredField("fd");
            field.setAccessible(true);
            field.setInt(fileDescriptor, Integer.parseInt(descriptor));
            return new FileOutputStream(fileDescriptor);
        } catch (ReflectiveOperationException | NumberFormatException error) {
            Runtime.getRuntime().halt(70);
            throw new AssertionError(error);
        }
    }

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

    private static void counterqEmit(String value) {
        byte[] frame = (value + "\n")
            .getBytes(StandardCharsets.UTF_8);
        try {
            COUNTERQ_RESULT_OUTPUT.write(frame);
            COUNTERQ_RESULT_OUTPUT.flush();
        } catch (IOException error) {
            Runtime.getRuntime().halt(70);
        }
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
        return f"new {java_type}{{" + ", ".join(_java_literal(item, inner) for item in value) + "}"
    raise UnsupportedExecutionSchema(f"Unsupported semantic type {semantic_type}")

"""Deterministic candidate starter code derived from trusted execution metadata."""

from __future__ import annotations

import re

from app.problems.content import (
    ExecutionDefinition,
    LanguageDefinition,
    SemanticType,
)

_CPP_TYPES: dict[SemanticType, str] = {
    "int": "int",
    "bool": "bool",
    "string": "string",
    "int[]": "vector<int>",
    "string[]": "vector<string>",
    "int[][]": "vector<vector<int>>",
    "string[][]": "vector<vector<string>>",
}
_PYTHON_TYPES: dict[SemanticType, str] = {
    "int": "int",
    "bool": "bool",
    "string": "str",
    "int[]": "list[int]",
    "string[]": "list[str]",
    "int[][]": "list[list[int]]",
    "string[][]": "list[list[str]]",
}
_JAVA_TYPES: dict[SemanticType, str] = {
    "int": "int",
    "bool": "boolean",
    "string": "String",
    "int[]": "int[]",
    "string[]": "String[]",
    "int[][]": "int[][]",
    "string[][]": "String[][]",
}


def starter_languages_for_execution(
    execution: ExecutionDefinition,
) -> dict[str, LanguageDefinition]:
    """Build non-solving scaffolds; no model-authored source reaches the candidate."""

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", execution.method_name) is None:
        raise ValueError("Execution method name is not a safe identifier")

    cpp_arguments = ", ".join(
        f"{_CPP_TYPES[item.type]} {item.name}" for item in execution.arguments
    )
    python_arguments = ", ".join(
        f"{item.name}: {_PYTHON_TYPES[item.type]}" for item in execution.arguments
    )
    java_arguments = ", ".join(
        f"{_JAVA_TYPES[item.type]} {item.name}" for item in execution.arguments
    )

    cpp_signature = (
        f"{_CPP_TYPES[execution.return_type]} {execution.method_name}({cpp_arguments})"
    )
    python_signature = (
        f"def {execution.method_name}(self, {python_arguments})"
        f" -> {_PYTHON_TYPES[execution.return_type]}"
    )
    java_signature = (
        f"public {_JAVA_TYPES[execution.return_type]} "
        f"{execution.method_name}({java_arguments})"
    )
    return {
        "cpp": LanguageDefinition(
            display_signature=cpp_signature,
            starter_code=(
                "class Solution {\n"
                "public:\n"
                f"    {cpp_signature} {{\n"
                '        throw logic_error("Not implemented");\n'
                "    }\n"
                "};"
            ),
        ),
        "python": LanguageDefinition(
            display_signature=python_signature,
            starter_code=(
                "class Solution:\n"
                f"    {python_signature}:\n"
                '        raise NotImplementedError("Not implemented")'
            ),
        ),
        "java": LanguageDefinition(
            display_signature=java_signature,
            starter_code=(
                "class Solution {\n"
                f"    {java_signature} {{\n"
                '        throw new UnsupportedOperationException("Not implemented");\n'
                "    }\n"
                "}"
            ),
        ),
    }

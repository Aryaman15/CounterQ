"""Bounded syntactic evidence derived from untrusted custom-problem text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.problems.content import SemanticType

ContradictableFindingCode = Literal[
    "MISSING_ARGUMENTS",
    "AMBIGUOUS_ARGUMENT_TYPES",
    "MISSING_RETURN_BEHAVIOR",
    "MISSING_EXAMPLE",
]


@dataclass(frozen=True)
class CustomProblemSourceArgument:
    name: str
    type: SemanticType

    def to_payload(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type}


@dataclass(frozen=True)
class CustomProblemSourceSignature:
    method_name: str
    arguments: tuple[CustomProblemSourceArgument, ...]
    return_type: SemanticType

    def to_payload(self) -> dict[str, object]:
        return {
            "method_name": self.method_name,
            "arguments": [argument.to_payload() for argument in self.arguments],
            "return_type": self.return_type,
        }


@dataclass(frozen=True)
class CustomProblemSourceEvidence:
    signature: CustomProblemSourceSignature | None
    has_explicit_return_directive: bool
    has_example_section: bool
    has_expected_output_example: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "signature": self.signature.to_payload() if self.signature is not None else None,
            "has_explicit_return_directive": self.has_explicit_return_directive,
            "has_example_section": self.has_example_section,
            "has_expected_output_example": self.has_expected_output_example,
        }


_CPP_SCALAR = r"(?:int|bool|(?:std::)?string)"
_CPP_VECTOR = (
    rf"(?:std::)?vector\s*<\s*(?:{_CPP_SCALAR}|"
    rf"(?:std::)?vector\s*<\s*{_CPP_SCALAR}\s*>\s*)>"
)
_JAVA_TYPE = r"(?:int|boolean|String)(?:\s*\[\s*\]){0,2}"
_C_LIKE_TYPE = rf"(?:{_CPP_VECTOR}|{_JAVA_TYPE}|bool|(?:std::)?string)"
_C_LIKE_SIGNATURE = re.compile(
    rf"(?<![A-Za-z0-9_:])(?P<return_type>{_C_LIKE_TYPE})\s+"
    r"(?P<method_name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<arguments>[^()\r\n]*)\)",
)
_PYTHON_SIGNATURE = re.compile(
    r"\bdef\s+(?P<method_name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<arguments>[^()\r\n]*)\)\s*->\s*(?P<return_type>[^:\r\n]+)",
)
_EXAMPLE_HEADING = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?examples?(?:\s+\d+)?\s*:\s*")
_EXAMPLE_INPUT = re.compile(r"(?im)^\s*input\s*:")
_EXAMPLE_OUTPUT = re.compile(r"(?im)^\s*(?:expected\s+)?output\s*:")
_RETURN_DIRECTIVE = re.compile(
    r"\breturn(?:s)?\s+(?:(?:the|a|an)\s+)?"
    r"(?:number|count|how\s+many|index|indices|value|values|bool|boolean|true|false|"
    r"string|array|list|matrix|sum|minimum|maximum|length|result|pair|pairs)\b",
    re.IGNORECASE,
)


def derive_custom_problem_source_evidence(value: str) -> CustomProblemSourceEvidence:
    """Derive only bounded syntax and explicit source markers; never problem semantics."""

    example_sections = list(_EXAMPLE_HEADING.finditer(value))
    has_expected_output_example = any(
        _EXAMPLE_INPUT.search(
            value[match.end() : example_sections[index + 1].start()]
            if index + 1 < len(example_sections)
            else value[match.end() :]
        )
        and _EXAMPLE_OUTPUT.search(
            value[match.end() : example_sections[index + 1].start()]
            if index + 1 < len(example_sections)
            else value[match.end() :]
        )
        for index, match in enumerate(example_sections)
    )
    return CustomProblemSourceEvidence(
        signature=parse_supported_function_signature(value),
        has_explicit_return_directive=_RETURN_DIRECTIVE.search(value) is not None,
        has_example_section=bool(example_sections),
        has_expected_output_example=has_expected_output_example,
    )


def parse_supported_function_signature(value: str) -> CustomProblemSourceSignature | None:
    """Recognize complete supported C++/Java/Python signatures without guessing."""

    candidates: set[CustomProblemSourceSignature] = set()
    for match in _C_LIKE_SIGNATURE.finditer(value):
        parsed = _parse_c_like_signature(match)
        if parsed is not None:
            candidates.add(parsed)
    for match in _PYTHON_SIGNATURE.finditer(value):
        parsed = _parse_python_signature(match)
        if parsed is not None:
            candidates.add(parsed)
    return next(iter(candidates)) if len(candidates) == 1 else None


def contradicted_normalization_findings(
    finding_codes: Iterable[str],
    evidence: CustomProblemSourceEvidence,
) -> tuple[ContradictableFindingCode, ...]:
    """Return only model blockers disproved by the bounded source evidence."""

    signature = evidence.signature
    contradicted: list[ContradictableFindingCode] = []
    for code in finding_codes:
        if code == "MISSING_RETURN_BEHAVIOR":
            if signature is not None and evidence.has_explicit_return_directive:
                contradicted.append("MISSING_RETURN_BEHAVIOR")
        elif code == "MISSING_ARGUMENTS":
            if signature is not None and signature.arguments:
                contradicted.append("MISSING_ARGUMENTS")
        elif code == "AMBIGUOUS_ARGUMENT_TYPES":
            if signature is not None and signature.arguments:
                contradicted.append("AMBIGUOUS_ARGUMENT_TYPES")
        elif code == "MISSING_EXAMPLE" and evidence.has_expected_output_example:
            contradicted.append("MISSING_EXAMPLE")
    return tuple(contradicted)


def _parse_c_like_signature(match: re.Match[str]) -> CustomProblemSourceSignature | None:
    return_type = _semantic_type(match.group("return_type"))
    arguments = _parse_c_like_arguments(match.group("arguments"))
    if return_type is None or arguments is None or not arguments:
        return None
    return CustomProblemSourceSignature(
        method_name=match.group("method_name"),
        arguments=arguments,
        return_type=return_type,
    )


def _parse_c_like_arguments(value: str) -> tuple[CustomProblemSourceArgument, ...] | None:
    parts = _split_arguments(value)
    if not parts:
        return None
    parsed: list[CustomProblemSourceArgument] = []
    for part in parts:
        name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", part)
        if name_match is None:
            return None
        type_source = part[: name_match.start()].strip()
        semantic_type = _semantic_type(type_source)
        if semantic_type is None:
            return None
        parsed.append(CustomProblemSourceArgument(name=name_match.group(1), type=semantic_type))
    if len({argument.name for argument in parsed}) != len(parsed):
        return None
    return tuple(parsed)


def _parse_python_signature(match: re.Match[str]) -> CustomProblemSourceSignature | None:
    return_type = _semantic_type(match.group("return_type"))
    if return_type is None:
        return None
    parsed: list[CustomProblemSourceArgument] = []
    for part in _split_arguments(match.group("arguments")):
        if part in {"self", "cls"}:
            continue
        argument_match = re.fullmatch(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<type>.+)", part)
        if argument_match is None:
            return None
        semantic_type = _semantic_type(argument_match.group("type"))
        if semantic_type is None:
            return None
        parsed.append(
            CustomProblemSourceArgument(name=argument_match.group("name"), type=semantic_type)
        )
    if not parsed or len({argument.name for argument in parsed}) != len(parsed):
        return None
    return CustomProblemSourceSignature(
        method_name=match.group("method_name"),
        arguments=tuple(parsed),
        return_type=return_type,
    )


def _split_arguments(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character in "<[":
            depth += 1
        elif character in ">]":
            depth -= 1
            if depth < 0:
                return ()
        elif character == "," and depth == 0:
            part = value[start:index].strip()
            if not part:
                return ()
            parts.append(part)
            start = index + 1
    if depth != 0:
        return ()
    final = value[start:].strip()
    if final:
        parts.append(final)
    return tuple(parts)


def _semantic_type(value: str) -> SemanticType | None:
    normalized = re.sub(r"\s+", "", value)
    normalized = re.sub(r"^(?:const|final)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.removesuffix("&&").removesuffix("&")
    normalized = normalized.replace("std::", "").replace("typing.", "").lower()
    mappings: dict[str, SemanticType] = {
        "int": "int",
        "bool": "bool",
        "boolean": "bool",
        "string": "string",
        "str": "string",
        "vector<int>": "int[]",
        "vector<string>": "string[]",
        "vector<vector<int>>": "int[][]",
        "vector<vector<string>>": "string[][]",
        "list[int]": "int[]",
        "list[str]": "string[]",
        "list[string]": "string[]",
        "list[list[int]]": "int[][]",
        "list[list[str]]": "string[][]",
        "list[list[string]]": "string[][]",
        "int[]": "int[]",
        "string[]": "string[]",
        "int[][]": "int[][]",
        "string[][]": "string[][]",
    }
    return mappings.get(normalized)

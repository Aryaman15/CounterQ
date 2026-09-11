"""Bounded syntactic evidence derived from untrusted custom-problem text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from app.problems.content import SemanticType, validate_semantic_value

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
class CustomProblemSourceExample:
    arguments: tuple[tuple[str, JsonValue], ...]
    expected_output: JsonValue

    def to_payload(self) -> dict[str, object]:
        return {
            "arguments": dict(self.arguments),
            "expected_output": self.expected_output,
        }


@dataclass(frozen=True)
class CustomProblemSourceEvidence:
    signature: CustomProblemSourceSignature | None
    has_explicit_return_directive: bool
    has_example_section: bool
    has_expected_output_example: bool
    parsed_visible_cases: tuple[CustomProblemSourceExample, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "signature": self.signature.to_payload() if self.signature is not None else None,
            "has_explicit_return_directive": self.has_explicit_return_directive,
            "has_example_section": self.has_example_section,
            "has_expected_output_example": self.has_expected_output_example,
            "parsed_visible_cases": [case.to_payload() for case in self.parsed_visible_cases],
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
_EXAMPLE_HEADING = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?examples?(?:[ \t]+\d+)?[ \t]*:[ \t]*(?:\r?\n|$)"
)
_EXAMPLE_INPUT = re.compile(r"(?im)^[ \t]*input[ \t]*:")
_EXAMPLE_OUTPUT = re.compile(r"(?im)^[ \t]*(?:expected[ \t]+)?output[ \t]*:")
_EXAMPLE_INPUT_LINE = re.compile(r"(?im)^[ \t]*input[ \t]*:[ \t]*(?P<value>[^\r\n]*)[ \t]*$")
_EXAMPLE_OUTPUT_LINE = re.compile(
    r"(?im)^[ \t]*(?:expected[ \t]+)?output[ \t]*:[ \t]*(?P<value>[^\r\n]*)[ \t]*$"
)
_RETURN_DIRECTIVE = re.compile(
    r"\breturn(?:s)?\s+(?:(?:the|a|an)\s+)?"
    r"(?:number|count|how\s+many|index|indices|value|values|bool|boolean|true|false|"
    r"string|array|list|matrix|sum|minimum|maximum|length|result|pair|pairs)\b",
    re.IGNORECASE,
)
_ARGUMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INTEGER_LITERAL = re.compile(r"-?(?:0|[1-9][0-9]*)")
_MAX_PARSED_EXAMPLES = 8
_MAX_LITERAL_CHARACTERS = 4_096
_MAX_LITERAL_VALUES = 256
_MAX_LITERAL_DEPTH = 4


def derive_custom_problem_source_evidence(value: str) -> CustomProblemSourceEvidence:
    """Derive only bounded syntax and explicit source markers; never problem semantics."""

    signature = parse_supported_function_signature(value)
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
        signature=signature,
        has_explicit_return_directive=_RETURN_DIRECTIVE.search(value) is not None,
        has_example_section=bool(example_sections),
        has_expected_output_example=has_expected_output_example,
        parsed_visible_cases=parse_supported_source_examples(
            value,
            signature,
            example_sections=example_sections,
        ),
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


def parse_supported_source_examples(
    value: str,
    signature: CustomProblemSourceSignature | None,
    *,
    example_sections: list[re.Match[str]] | None = None,
) -> tuple[CustomProblemSourceExample, ...]:
    """Parse only bounded labeled examples that exactly match a supported signature."""

    if signature is None:
        return ()
    sections = (
        list(_EXAMPLE_HEADING.finditer(value)) if example_sections is None else example_sections
    )
    if not sections or len(sections) > _MAX_PARSED_EXAMPLES:
        return ()
    parsed: list[CustomProblemSourceExample] = []
    for index, heading in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(value)
        example = _parse_source_example(value[heading.end() : end], signature)
        if example is not None:
            parsed.append(example)
    return tuple(parsed)


def parse_supported_literal(value: str) -> JsonValue | None:
    """Parse the supported JSON/Python-like literal subset without evaluating code."""

    if not value or len(value) > _MAX_LITERAL_CHARACTERS:
        return None
    try:
        return _LiteralParser(value).parse()
    except _LiteralParseError:
        return None


def _parse_source_example(
    section: str,
    signature: CustomProblemSourceSignature,
) -> CustomProblemSourceExample | None:
    input_matches = list(_EXAMPLE_INPUT_LINE.finditer(section))
    output_matches = list(_EXAMPLE_OUTPUT_LINE.finditer(section))
    if (
        len(input_matches) != 1
        or len(output_matches) != 1
        or input_matches[0].end() > output_matches[0].start()
    ):
        return None
    parts = _split_top_level(input_matches[0].group("value"), delimiter=",")
    if not parts:
        return None
    argument_values: dict[str, JsonValue] = {}
    for part in parts:
        assignment = _split_assignment(part)
        if assignment is None:
            return None
        name, literal_source = assignment
        if _ARGUMENT_NAME.fullmatch(name) is None or name in argument_values:
            return None
        parsed_value = parse_supported_literal(literal_source)
        if parsed_value is None:
            return None
        argument_values[name] = parsed_value

    argument_types = {argument.name: argument.type for argument in signature.arguments}
    if set(argument_values) != set(argument_types):
        return None
    if any(
        not validate_semantic_value(argument_values[name], semantic_type)
        for name, semantic_type in argument_types.items()
    ):
        return None
    expected_output = parse_supported_literal(output_matches[0].group("value"))
    if expected_output is None or not validate_semantic_value(
        expected_output, signature.return_type
    ):
        return None
    return CustomProblemSourceExample(
        arguments=tuple(
            (argument.name, argument_values[argument.name]) for argument in signature.arguments
        ),
        expected_output=expected_output,
    )


def _split_top_level(value: str, *, delimiter: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return ()
        elif character == delimiter and depth == 0:
            part = value[start:index].strip()
            if not part:
                return ()
            parts.append(part)
            start = index + 1
    if quote is not None or escaped or depth != 0:
        return ()
    final = value[start:].strip()
    if not final:
        return ()
    parts.append(final)
    return tuple(parts)


def _split_assignment(value: str) -> tuple[str, str] | None:
    positions: list[int] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return None
        elif character == "=" and depth == 0:
            positions.append(index)
    if quote is not None or escaped or depth != 0 or len(positions) != 1:
        return None
    position = positions[0]
    name = value[:position].strip()
    literal_source = value[position + 1 :].strip()
    return (name, literal_source) if name and literal_source else None


class _LiteralParseError(ValueError):
    pass


class _LiteralParser:
    def __init__(self, value: str) -> None:
        self._value = value
        self._index = 0
        self._parsed_values = 0

    def parse(self) -> JsonValue:
        parsed = self._parse_value(depth=0)
        self._skip_whitespace()
        if self._index != len(self._value):
            raise _LiteralParseError
        return parsed

    def _parse_value(self, *, depth: int) -> JsonValue:
        self._skip_whitespace()
        self._parsed_values += 1
        if self._parsed_values > _MAX_LITERAL_VALUES or self._index >= len(self._value):
            raise _LiteralParseError
        character = self._value[self._index]
        if character in {'"', "'"}:
            return self._parse_string()
        if character == "[":
            return self._parse_array(depth=depth)
        for token, parsed in (("true", True), ("false", False), ("True", True), ("False", False)):
            if self._value.startswith(token, self._index):
                self._index += len(token)
                return parsed
        match = _INTEGER_LITERAL.match(self._value, self._index)
        if match is None:
            raise _LiteralParseError
        self._index = match.end()
        return int(match.group(0))

    def _parse_array(self, *, depth: int) -> list[JsonValue]:
        if depth >= _MAX_LITERAL_DEPTH:
            raise _LiteralParseError
        self._index += 1
        self._skip_whitespace()
        values: list[JsonValue] = []
        if self._consume("]"):
            return values
        while True:
            values.append(self._parse_value(depth=depth + 1))
            self._skip_whitespace()
            if self._consume("]"):
                return values
            if not self._consume(","):
                raise _LiteralParseError
            self._skip_whitespace()
            if self._index >= len(self._value) or self._value[self._index] == "]":
                raise _LiteralParseError

    def _parse_string(self) -> str:
        quote = self._value[self._index]
        self._index += 1
        characters: list[str] = []
        escapes = {
            '"': '"',
            "'": "'",
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while self._index < len(self._value):
            character = self._value[self._index]
            self._index += 1
            if character == quote:
                return "".join(characters)
            if ord(character) < 0x20:
                raise _LiteralParseError
            if character != "\\":
                characters.append(character)
                continue
            if self._index >= len(self._value):
                raise _LiteralParseError
            escaped = self._value[self._index]
            self._index += 1
            if escaped == "u":
                codepoint = self._value[self._index : self._index + 4]
                if len(codepoint) != 4 or re.fullmatch(r"[0-9A-Fa-f]{4}", codepoint) is None:
                    raise _LiteralParseError
                characters.append(chr(int(codepoint, 16)))
                self._index += 4
            elif escaped in escapes:
                characters.append(escapes[escaped])
            else:
                raise _LiteralParseError
        raise _LiteralParseError

    def _skip_whitespace(self) -> None:
        while self._index < len(self._value) and self._value[self._index] in " \t":
            self._index += 1

    def _consume(self, expected: str) -> bool:
        if self._index < len(self._value) and self._value[self._index] == expected:
            self._index += 1
            return True
        return False


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

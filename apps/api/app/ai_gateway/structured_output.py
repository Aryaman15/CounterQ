from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictReasoningOutputModel(BaseModel):
    """Base for AI Gateway structured outputs.

    OpenAI strict Structured Outputs requires object schemas to opt out of extra
    keys and to require every declared field. Semantically optional values should
    be represented as `T | None` without a default so the key is still required.
    """

    model_config = ConfigDict(extra="forbid")


class StructuredOutputSchemaError(ValueError):
    pass


UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
    }
)


def validate_strict_reasoning_schema(schema: Mapping[str, Any]) -> None:
    if schema.get("anyOf") is not None:
        raise StructuredOutputSchemaError("Root structured output schema must not use anyOf")
    if schema.get("type") != "object":
        raise StructuredOutputSchemaError("Root structured output schema must be an object")
    _validate_schema_node(schema, path="$", root_schema=schema)


def _validate_schema_node(
    node: Mapping[str, Any],
    *,
    path: str,
    root_schema: Mapping[str, Any],
) -> None:
    unsupported = sorted(UNSUPPORTED_SCHEMA_KEYWORDS.intersection(node))
    if unsupported:
        raise StructuredOutputSchemaError(
            f"Unsupported structured output schema keyword at {path}: {unsupported[0]}"
        )

    ref = node.get("$ref")
    if isinstance(ref, str):
        target = _resolve_local_ref(root_schema, ref)
        _validate_schema_node(target, path=f"{path}->{ref}", root_schema=root_schema)
        return

    if node.get("type") == "object":
        if node.get("additionalProperties") is not False:
            raise StructuredOutputSchemaError(
                f"Object structured output schema at {path} must set additionalProperties=false"
            )
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            properties = {}
        required = node.get("required")
        if not isinstance(required, list):
            raise StructuredOutputSchemaError(
                f"Object structured output schema at {path} must list required fields"
            )
        missing_required = sorted(set(properties) - set(required))
        if missing_required:
            raise StructuredOutputSchemaError(
                f"Object structured output schema at {path} has non-required property "
                f"{missing_required[0]}"
            )
        for name, child in properties.items():
            if isinstance(child, Mapping):
                _validate_schema_node(
                    child,
                    path=f"{path}.properties.{name}",
                    root_schema=root_schema,
                )

    items = node.get("items")
    if isinstance(items, Mapping):
        _validate_schema_node(items, path=f"{path}.items", root_schema=root_schema)

    for keyword in ("anyOf", "oneOf"):
        variants = node.get(keyword)
        if isinstance(variants, list):
            for index, variant in enumerate(variants):
                if isinstance(variant, Mapping):
                    _validate_schema_node(
                        variant,
                        path=f"{path}.{keyword}[{index}]",
                        root_schema=root_schema,
                    )

    defs = node.get("$defs")
    if isinstance(defs, Mapping):
        for name, definition in defs.items():
            if isinstance(definition, Mapping):
                _validate_schema_node(
                    definition,
                    path=f"{path}.$defs.{name}",
                    root_schema=root_schema,
                )


def _resolve_local_ref(root_schema: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise StructuredOutputSchemaError(f"Only local schema refs are supported: {ref}")
    current: Any = root_schema
    for part in ref.removeprefix("#/").split("/"):
        if not isinstance(current, Mapping):
            raise StructuredOutputSchemaError(f"Invalid structured output schema ref: {ref}")
        current = current.get(part)
    if not isinstance(current, Mapping):
        raise StructuredOutputSchemaError(f"Invalid structured output schema ref: {ref}")
    return current

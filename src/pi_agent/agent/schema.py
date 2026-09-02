from __future__ import annotations

import re
from typing import Any


class ToolArgumentsError(ValueError):
    """Tool arguments do not satisfy the declared JSON Schema."""


def validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Validate the JSON-Schema subset used by pi tool declarations.

    The runtime intentionally owns validation rather than relying on model
    compliance. Unsupported schema keywords are ignored, while all implemented
    keywords fail explicitly and include the argument path.
    """

    if "allOf" in schema:
        for child in _schema_list(schema["allOf"], path, "allOf"):
            validate_json_schema(value, child, path=path)
    if "anyOf" in schema:
        children = _schema_list(schema["anyOf"], path, "anyOf")
        if not any(_is_valid(value, child, path) for child in children):
            raise ToolArgumentsError(f"{path}: value does not match anyOf")
    if "oneOf" in schema:
        children = _schema_list(schema["oneOf"], path, "oneOf")
        matches = sum(_is_valid(value, child, path) for child in children)
        if matches != 1:
            raise ToolArgumentsError(f"{path}: value must match exactly one oneOf schema")
    if (
        "not" in schema
        and isinstance(schema["not"], dict)
        and _is_valid(value, schema["not"], path)
    ):
        raise ToolArgumentsError(f"{path}: value matches forbidden schema")

    if "const" in schema and value != schema["const"]:
        raise ToolArgumentsError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, list):
            raise ToolArgumentsError(f"{path}: schema enum must be an array")
        if value not in enum_values:
            raise ToolArgumentsError(f"{path}: expected one of {enum_values!r}")

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise ToolArgumentsError(f"{path}: schema type must be a string or string array")
        if not any(_matches_type(value, item) for item in expected_types):
            names = " or ".join(expected_types)
            raise ToolArgumentsError(f"{path}: expected {names}, got {_json_type(value)}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ToolArgumentsError(f"{path}: schema required must be an array")
        for key in required:
            if isinstance(key, str) and key not in value:
                raise ToolArgumentsError(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ToolArgumentsError(f"{path}: schema properties must be an object")
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                validate_json_schema(child_value, child_schema, path=f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ToolArgumentsError(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(
                    child_value,
                    schema["additionalProperties"],
                    path=f"{path}.{key}",
                )
        _check_size(value, schema, path, "minProperties", "maxProperties")

    if isinstance(value, list):
        _check_size(value, schema, path, "minItems", "maxItems")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if item in value[:index]:
                    raise ToolArgumentsError(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, str):
        _check_size(value, schema, path, "minLength", "maxLength")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ToolArgumentsError(f"{path}: string does not match pattern {pattern!r}")

    if _is_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if _is_number(minimum) and value < minimum:
            raise ToolArgumentsError(f"{path}: value is below minimum {minimum}")
        if _is_number(maximum) and value > maximum:
            raise ToolArgumentsError(f"{path}: value is above maximum {maximum}")
        if _is_number(exclusive_minimum) and value <= exclusive_minimum:
            raise ToolArgumentsError(f"{path}: value must be greater than {exclusive_minimum}")
        if _is_number(exclusive_maximum) and value >= exclusive_maximum:
            raise ToolArgumentsError(f"{path}: value must be less than {exclusive_maximum}")


def _schema_list(value: Any, path: str, keyword: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ToolArgumentsError(f"{path}: schema {keyword} must be an array of objects")
    return value


def _is_valid(value: Any, schema: dict[str, Any], path: str) -> bool:
    try:
        validate_json_schema(value, schema, path=path)
    except ToolArgumentsError:
        return False
    return True


def _check_size(
    value: Any,
    schema: dict[str, Any],
    path: str,
    minimum_key: str,
    maximum_key: str,
) -> None:
    minimum = schema.get(minimum_key)
    maximum = schema.get(maximum_key)
    if isinstance(minimum, int) and len(value) < minimum:
        raise ToolArgumentsError(f"{path}: length is below {minimum_key}={minimum}")
    if isinstance(maximum, int) and len(value) > maximum:
        raise ToolArgumentsError(f"{path}: length is above {maximum_key}={maximum}")


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": _is_number(value),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

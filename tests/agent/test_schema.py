from __future__ import annotations

import pytest

from pi_agent.agent import ToolArgumentsError, validate_json_schema


def test_schema_validator_enforces_nested_required_and_additional_properties() -> None:
    schema = {
        "type": "object",
        "required": ["path", "options"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "options": {
                "type": "object",
                "required": ["limit"],
                "properties": {"limit": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    validate_json_schema({"path": "README.md", "options": {"limit": 10}}, schema)

    with pytest.raises(ToolArgumentsError, match="missing required property 'limit'"):
        validate_json_schema({"path": "README.md", "options": {}}, schema)
    with pytest.raises(ToolArgumentsError, match="unexpected property 'extra'"):
        validate_json_schema(
            {"path": "README.md", "options": {"limit": 10}, "extra": True},
            schema,
        )
    with pytest.raises(ToolArgumentsError, match="expected integer"):
        validate_json_schema({"path": "README.md", "options": {"limit": True}}, schema)


def test_schema_validator_supports_composition_enum_and_array_constraints() -> None:
    schema = {
        "oneOf": [
            {"type": "string", "enum": ["auto", "none"]},
            {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "integer"},
            },
        ]
    }
    validate_json_schema("auto", schema)
    validate_json_schema([1, 2], schema)
    with pytest.raises(ToolArgumentsError, match="exactly one"):
        validate_json_schema([], schema)

"""Compatibility imports for the Phase 17 draft JSON module."""

from .json_event import parse_json_line, serialize_json_line, to_json_event

__all__ = ["parse_json_line", "serialize_json_line", "to_json_event"]

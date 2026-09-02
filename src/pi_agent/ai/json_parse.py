from __future__ import annotations

import json
import re
from typing import cast

from .types import JsonValue

_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d*)?(?:[eE][+-]?\d*)?")
_MISSING = object()


def parse_streaming_json(raw: str) -> JsonValue:
    """Parse complete JSON or the useful prefix of an in-flight JSON value.

    Tool-call arguments are exposed on delta events for rendering, but the
    Agent Loop still waits for the provider's terminal tool-call event before
    validation or execution.
    """

    if not raw.strip():
        return {}
    try:
        return cast(JsonValue, json.loads(raw))
    except json.JSONDecodeError:
        parser = _PartialJsonParser(raw)
        value = parser.parse_value()
        return {} if value is _MISSING else cast(JsonValue, value)


class _PartialJsonParser:
    __slots__ = ("index", "raw")

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.index = 0

    def parse_value(self) -> JsonValue | object:
        self._skip_whitespace()
        if self.index >= len(self.raw):
            return _MISSING
        char = self.raw[self.index]
        if char == "{":
            return self._parse_object()
        if char == "[":
            return self._parse_array()
        if char == '"':
            value, _complete = self._parse_string()
            return value
        if char in "-0123456789":
            return self._parse_number()
        literals: tuple[tuple[str, JsonValue], ...] = (
            ("true", True),
            ("false", False),
            ("null", None),
        )
        for literal, literal_value in literals:
            remainder = self.raw[self.index :]
            if remainder.startswith(literal):
                self.index += len(literal)
                return literal_value
            if literal.startswith(remainder):
                self.index = len(self.raw)
                return _MISSING
        return _MISSING

    def _parse_object(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        self.index += 1
        while True:
            self._skip_whitespace()
            if self.index >= len(self.raw) or self.raw[self.index] == "}":
                if self.index < len(self.raw):
                    self.index += 1
                return result
            if self.raw[self.index] != '"':
                return result
            key, key_complete = self._parse_string()
            if not key_complete:
                return result
            self._skip_whitespace()
            if self.index >= len(self.raw) or self.raw[self.index] != ":":
                return result
            self.index += 1
            before_value = self.index
            value = self.parse_value()
            if value is _MISSING:
                self.index = before_value
                return result
            result[key] = cast(JsonValue, value)
            self._skip_whitespace()
            if self.index >= len(self.raw):
                return result
            if self.raw[self.index] == "}":
                self.index += 1
                return result
            if self.raw[self.index] != ",":
                return result
            self.index += 1

    def _parse_array(self) -> list[JsonValue]:
        result: list[JsonValue] = []
        self.index += 1
        while True:
            self._skip_whitespace()
            if self.index >= len(self.raw) or self.raw[self.index] == "]":
                if self.index < len(self.raw):
                    self.index += 1
                return result
            value = self.parse_value()
            if value is _MISSING:
                return result
            result.append(cast(JsonValue, value))
            self._skip_whitespace()
            if self.index >= len(self.raw):
                return result
            if self.raw[self.index] == "]":
                self.index += 1
                return result
            if self.raw[self.index] != ",":
                return result
            self.index += 1

    def _parse_string(self) -> tuple[str, bool]:
        self.index += 1
        result: list[str] = []
        escapes = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while self.index < len(self.raw):
            char = self.raw[self.index]
            self.index += 1
            if char == '"':
                return "".join(result), True
            if char != "\\":
                result.append(char)
                continue
            if self.index >= len(self.raw):
                return "".join(result), False
            escaped = self.raw[self.index]
            self.index += 1
            if escaped == "u":
                digits = self.raw[self.index : self.index + 4]
                if len(digits) < 4 or any(char not in "0123456789abcdefABCDEF" for char in digits):
                    return "".join(result), False
                result.append(chr(int(digits, 16)))
                self.index += 4
            else:
                result.append(escapes.get(escaped, escaped))
        return "".join(result), False

    def _parse_number(self) -> int | float | object:
        match = _NUMBER.match(self.raw, self.index)
        if match is None:
            return _MISSING
        token = match.group(0)
        if token.endswith((".", "e", "E", "+", "-")):
            token = token.rstrip(".eE+-")
        if not token or token == "-":
            return _MISSING
        self.index = match.end()
        try:
            return float(token) if any(char in token for char in ".eE") else int(token)
        except ValueError:
            return _MISSING

    def _skip_whitespace(self) -> None:
        while self.index < len(self.raw) and self.raw[self.index].isspace():
            self.index += 1

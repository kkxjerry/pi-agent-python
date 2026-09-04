from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .types import AttributeValue

_DEFAULT_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "id_token",
        "password",
        "refresh_token",
        "secret",
        "token",
        "x_api_key",
    }
)
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_SK_TOKEN = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)


@dataclass(slots=True)
class TelemetryRedactor:
    secrets: tuple[str, ...] = ()
    secret_keys: frozenset[str] = field(default_factory=lambda: _DEFAULT_SECRET_KEYS)
    replacement: str = "[REDACTED]"

    def with_secrets(self, values: Iterable[str]) -> TelemetryRedactor:
        return TelemetryRedactor(
            tuple(dict.fromkeys((*self.secrets, *(item for item in values if item)))),
            self.secret_keys,
            self.replacement,
        )

    def value(self, value: Any, *, key: str | None = None) -> Any:
        return self.redact(value, key=key)

    def attributes(self, values: Mapping[str, Any]) -> dict[str, AttributeValue]:
        redacted = self.redact(values)
        return cast(
            dict[str, AttributeValue],
            {
                str(key): item
                if item is None or isinstance(item, str | int | float | bool)
                else str(item)
                for key, item in redacted.items()
            },
        )

    def redact(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and _normalize_key(key) in self.secret_keys:
            return self.replacement if value is not None else None
        if isinstance(value, str):
            result = value
            for secret in sorted((item for item in self.secrets if item), key=len, reverse=True):
                result = result.replace(secret, self.replacement)
            result = _BEARER.sub(rf"\1{self.replacement}", result)
            result = _SK_TOKEN.sub(self.replacement, result)
            return result
        if isinstance(value, Mapping):
            return {
                str(child_key): self.redact(child_value, key=str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, set):
            return {self.redact(item) for item in value}
        return value


def _normalize_key(value: str) -> str:
    return value.casefold().replace("-", "_").replace(".", "_")


def redact_payload(value: Any, redactor: TelemetryRedactor | None = None) -> Any:
    return (redactor or DEFAULT_REDACTOR).redact(value)


DEFAULT_REDACTOR = TelemetryRedactor()


__all__ = [
    "DEFAULT_REDACTOR",
    "TelemetryRedactor",
    "redact_payload",
]

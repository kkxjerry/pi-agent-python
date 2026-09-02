from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from .types import JsonValue


@dataclass(frozen=True, slots=True)
class ProviderErrorDetails:
    message: str
    status_code: int | None = None
    code: str | None = None
    retryable: bool = False
    retry_after: float | None = None
    headers: dict[str, str] = field(default_factory=dict)
    raw: JsonValue | None = None
    response_started: bool = False

    def diagnostic(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "type": "provider_error",
            "message": self.message,
            "retryable": self.retryable,
            "responseStarted": self.response_started,
        }
        if self.status_code is not None:
            value["statusCode"] = self.status_code
        if self.code is not None:
            value["code"] = self.code
        if self.retry_after is not None:
            value["retryAfterSeconds"] = self.retry_after
        if self.headers:
            value["headers"] = cast(dict[str, JsonValue], dict(self.headers))
        if self.raw is not None:
            value["raw"] = self.raw
        return value


class ProviderRequestError(RuntimeError):
    def __init__(self, details: ProviderErrorDetails) -> None:
        super().__init__(details.message)
        self.details = details

    def with_response_started(self, started: bool) -> ProviderRequestError:
        if self.details.response_started == started:
            return self
        return ProviderRequestError(
            ProviderErrorDetails(
                message=self.details.message,
                status_code=self.details.status_code,
                code=self.details.code,
                retryable=self.details.retryable,
                retry_after=self.details.retry_after,
                headers=self.details.headers,
                raw=self.details.raw,
                response_started=started,
            )
        )


def provider_error_from_payload(
    payload: Any,
    *,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
    retry_after: float | None = None,
    response_started: bool = False,
) -> ProviderRequestError:
    raw = _json_value(payload)
    message = _error_field(payload, "message") or str(payload)
    code = _error_field(payload, "code") or _error_field(payload, "type")
    retryable = (
        status_code is None
        or status_code in {408, 409, 425, 429}
        or (status_code is not None and 500 <= status_code <= 599)
    )
    return ProviderRequestError(
        ProviderErrorDetails(
            message=message,
            status_code=status_code,
            code=code,
            retryable=retryable,
            retry_after=retry_after,
            headers=headers or {},
            raw=raw,
            response_started=response_started,
        )
    )


def transport_error(message: str, *, raw: Any = None) -> ProviderRequestError:
    return ProviderRequestError(
        ProviderErrorDetails(
            message=message,
            retryable=True,
            raw=_json_value(raw),
        )
    )


def _error_field(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, (str, int, float)):
            return str(value)
        nested = payload.get("error")
        if nested is not payload:
            return _error_field(nested, key)
    return None


def _json_value(value: Any) -> JsonValue | None:
    if value is None:
        return None
    try:
        return cast(JsonValue, json.loads(json.dumps(value, default=str)))
    except (TypeError, ValueError):
        return str(value)

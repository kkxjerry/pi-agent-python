from __future__ import annotations

import asyncio
import contextvars
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .types import (
    AttributeValue,
    MetricKind,
    MetricPoint,
    SpanData,
    SpanKind,
    SpanStatus,
    TelemetryEvent,
    TelemetryExporter,
    json_safe_attributes,
)

_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "pi_agent_current_span",
    default=None,
)


@dataclass(slots=True)
class Span:
    tracer: Tracer
    name: str
    kind: SpanKind = "internal"
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    parent: Span | None = None
    trace_id: str = field(default_factory=lambda: secrets.token_hex(16))
    span_id: str = field(default_factory=lambda: secrets.token_hex(8))
    start_ns: int = field(default_factory=time.time_ns)
    status: SpanStatus = "unset"
    _events: list[TelemetryEvent] = field(default_factory=list)
    _ended: bool = False
    _error_type: str | None = None
    _error_message: str | None = None

    def __post_init__(self) -> None:
        if self.parent is not None:
            self.trace_id = self.parent.trace_id

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes.update(json_safe_attributes({key: value}))

    def set_attributes(self, values: dict[str, Any]) -> None:
        self.attributes.update(json_safe_attributes(values))

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self._events.append(
            TelemetryEvent(
                name,
                time.time_ns(),
                json_safe_attributes(attributes or {}),
            )
        )

    def record_exception(self, error: BaseException) -> None:
        self.status = "error"
        self._error_type = type(error).__name__
        self._error_message = str(error)
        self.add_event(
            "exception",
            {"exception.type": self._error_type, "exception.message": self._error_message},
        )

    async def end(self, status: SpanStatus | None = None) -> SpanData:
        if self._ended:
            raise RuntimeError(f"span {self.name!r} already ended")
        self._ended = True
        if status is not None:
            self.status = status
        elif self.status == "unset":
            self.status = "ok"
        data = SpanData(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent.span_id if self.parent is not None else None,
            name=self.name,
            kind=self.kind,
            start_ns=self.start_ns,
            end_ns=time.time_ns(),
            status=self.status,
            attributes=dict(self.attributes),
            events=tuple(self._events),
            error_type=self._error_type,
            error_message=self._error_message,
        )
        await self.tracer._export_span(data)
        return data


class Tracer:
    def __init__(
        self,
        exporter: TelemetryExporter,
        *,
        service_name: str = "pi-agent-python",
        default_attributes: dict[str, Any] | None = None,
    ) -> None:
        self.exporter = exporter
        self.service_name = service_name
        self.default_attributes = json_safe_attributes(default_attributes or {})
        self._lock = asyncio.Lock()
        self._closed = False

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> Span:
        if self._closed:
            raise RuntimeError("tracer is closed")
        selected_parent = parent if parent is not None else _current_span.get()
        values = {
            "service.name": self.service_name,
            **self.default_attributes,
            **json_safe_attributes(attributes or {}),
        }
        return Span(self, name, kind, values, selected_parent)

    @asynccontextmanager
    async def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: dict[str, Any] | None = None,
    ) -> AsyncIterator[Span]:
        span = self.start_span(name, kind=kind, attributes=attributes)
        token = _current_span.set(span)
        try:
            yield span
        except asyncio.CancelledError:
            await span.end("cancelled")
            raise
        except Exception as exc:
            span.record_exception(exc)
            await span.end("error")
            raise
        else:
            await span.end("ok")
        finally:
            _current_span.reset(token)

    async def _export_span(self, span: SpanData) -> None:
        async with self._lock:
            await self.exporter.export_spans((span,))

    async def shutdown(self) -> None:
        if self._closed:
            return
        async with self._lock:
            await self.exporter.shutdown()
            self._closed = True


class Meter:
    def __init__(
        self,
        exporter: TelemetryExporter,
        *,
        default_attributes: dict[str, Any] | None = None,
    ) -> None:
        self.exporter = exporter
        self.default_attributes = json_safe_attributes(default_attributes or {})
        self._lock = asyncio.Lock()

    async def record(
        self,
        name: str,
        value: float,
        *,
        kind: MetricKind = "counter",
        attributes: dict[str, Any] | None = None,
    ) -> MetricPoint:
        point = MetricPoint(
            name,
            kind,
            float(value),
            time.time_ns(),
            {
                **self.default_attributes,
                **json_safe_attributes(attributes or {}),
            },
        )
        async with self._lock:
            await self.exporter.export_metrics((point,))
        return point

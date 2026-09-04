from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

AttributeValue: TypeAlias = str | int | float | bool | None
SpanKind: TypeAlias = Literal["internal", "client", "server", "tool", "model"]
SpanStatus: TypeAlias = Literal["unset", "ok", "error", "cancelled"]
MetricKind: TypeAlias = Literal["counter", "histogram", "gauge"]


@dataclass(slots=True, frozen=True)
class SpanContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None


@dataclass(slots=True, frozen=True)
class TelemetryEvent:
    name: str
    timestamp_ns: int
    attributes: dict[str, AttributeValue] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SpanData:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    start_ns: int
    end_ns: int
    status: SpanStatus
    attributes: dict[str, AttributeValue]
    events: tuple[TelemetryEvent, ...]
    error_type: str | None = None
    error_message: str | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0, self.end_ns - self.start_ns) / 1_000_000_000

    @property
    def duration_ms(self) -> float:
        return self.duration_seconds * 1000

    @property
    def context(self) -> SpanContext:
        return SpanContext(self.trace_id, self.span_id, self.parent_span_id)

    @property
    def start_time_ns(self) -> int:
        return self.start_ns

    @property
    def end_time_ns(self) -> int:
        return self.end_ns


@dataclass(slots=True, frozen=True)
class MetricPoint:
    name: str
    kind: MetricKind
    value: float
    timestamp_ns: int
    attributes: dict[str, AttributeValue] = field(default_factory=dict)


SpanEvent = TelemetryEvent


@dataclass(slots=True, frozen=True)
class TelemetryDiagnostic:
    operation: str
    message: str
    component: str


class TelemetryExporter(Protocol):
    async def export_spans(self, spans: tuple[SpanData, ...]) -> None: ...

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None: ...

    async def shutdown(self) -> None: ...


class SpanProcessor(Protocol):
    async def on_end(self, span: SpanData) -> None: ...

    async def force_flush(self) -> None: ...

    async def shutdown(self) -> None: ...


class TelemetryError(RuntimeError):
    pass


def json_safe_attributes(values: dict[str, Any]) -> dict[str, AttributeValue]:
    result: dict[str, AttributeValue] = {}
    for key, value in values.items():
        if value is None or isinstance(value, str | int | float | bool):
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result

from __future__ import annotations

import asyncio
import contextvars
import secrets
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .redaction import DEFAULT_REDACTOR, TelemetryRedactor
from .types import (
    SpanContext,
    SpanData,
    SpanEvent,
    SpanKind,
    SpanProcessor,
    SpanStatus,
    TelemetryDiagnostic,
    TelemetryExporter,
)

_CURRENT_SPAN: contextvars.ContextVar[SpanContext | None] = contextvars.ContextVar(
    "pi_agent_current_span",
    default=None,
)


@dataclass(slots=True)
class Span:
    provider: TracerProvider
    name: str
    context: SpanContext
    kind: SpanKind
    start_time_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    status: SpanStatus = "unset"
    error_type: str | None = None
    error_message: str | None = None
    _ended: bool = False

    def set_attribute(self, key: str, value: Any) -> None:
        if not self._ended:
            self.attributes[key] = value

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        if not self._ended:
            self.events.append(
                SpanEvent(
                    name,
                    time.time_ns(),
                    self.provider.redactor.attributes(attributes or {}),
                )
            )

    def record_exception(self, error: BaseException) -> None:
        if self._ended:
            return
        self.status = "error"
        self.error_type = type(error).__name__
        self.error_message = str(error)
        self.add_event(
            "exception",
            {
                "exception.type": self.error_type,
                "exception.message": self.error_message,
            },
        )

    def end(self, *, status: SpanStatus | None = None) -> SpanData | None:
        if self._ended:
            return None
        self._ended = True
        if status is not None:
            self.status = status
        if self.status == "unset":
            self.status = "ok"
        data = SpanData(
            trace_id=self.context.trace_id,
            span_id=self.context.span_id,
            parent_span_id=self.context.parent_span_id,
            name=self.name,
            kind=self.kind,
            start_ns=self.start_time_ns,
            end_ns=time.time_ns(),
            status=self.status,
            attributes=self.provider.redactor.attributes(self.attributes),
            events=tuple(self.events),
            error_type=self.error_type,
            error_message=(
                None
                if self.error_message is None
                else str(self.provider.redactor.value(self.error_message))
            ),
        )
        self.provider.emit(data)
        return data


class Tracer:
    def __init__(self, provider: TracerProvider, name: str) -> None:
        self.provider = provider
        self.name = name

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: SpanContext | None = None,
    ) -> Span:
        actual_parent = parent if parent is not None else _CURRENT_SPAN.get()
        context = SpanContext(
            trace_id=actual_parent.trace_id if actual_parent is not None else secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            parent_span_id=actual_parent.span_id if actual_parent is not None else None,
        )
        values = {"instrumentation.scope": self.name, **dict(attributes or {})}
        return Span(
            provider=self.provider,
            name=name,
            context=context,
            kind=kind,
            start_time_ns=time.time_ns(),
            attributes=values,
        )

    @contextmanager
    def use_span(self, span: Span, *, end_on_exit: bool = True) -> Iterator[Span]:
        token = _CURRENT_SPAN.set(span.context)
        try:
            yield span
        except BaseException as exc:
            span.record_exception(exc)
            raise
        finally:
            _CURRENT_SPAN.reset(token)
            if end_on_exit:
                span.end()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: SpanContext | None = None,
    ) -> Iterator[Span]:
        span = self.start_span(name, kind=kind, attributes=attributes, parent=parent)
        with self.use_span(span):
            yield span


class SimpleSpanProcessor:
    def __init__(self, exporter: TelemetryExporter) -> None:
        self.exporter = exporter

    async def on_end(self, span: SpanData) -> None:
        await self.exporter.export_spans((span,))

    async def force_flush(self) -> None:
        return None

    async def shutdown(self) -> None:
        await self.exporter.shutdown()


class BatchSpanProcessor:
    def __init__(
        self,
        exporter: TelemetryExporter,
        *,
        max_batch_size: int = 64,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        self.exporter = exporter
        self.max_batch_size = max_batch_size
        self._pending: list[SpanData] = []
        self._lock = asyncio.Lock()

    async def on_end(self, span: SpanData) -> None:
        async with self._lock:
            self._pending.append(span)
            batch = (
                tuple(self._pending[: self.max_batch_size])
                if len(self._pending) >= self.max_batch_size
                else ()
            )
            if batch:
                del self._pending[: len(batch)]
        if batch:
            await self.exporter.export_spans(batch)

    async def force_flush(self) -> None:
        async with self._lock:
            batch = tuple(self._pending)
            self._pending.clear()
        if batch:
            await self.exporter.export_spans(batch)

    async def shutdown(self) -> None:
        await self.force_flush()
        await self.exporter.shutdown()


class TracerProvider:
    """Failure-isolated span fanout used by Agent and Provider instrumentation."""

    def __init__(
        self,
        processors: Sequence[SpanProcessor] = (),
        *,
        redactor: TelemetryRedactor | None = None,
    ) -> None:
        self.processors = tuple(processors)
        self.redactor = redactor or DEFAULT_REDACTOR
        self._tasks: set[asyncio.Task[None]] = set()
        self._diagnostics: list[TelemetryDiagnostic] = []
        self._closed = False

    @property
    def diagnostics(self) -> tuple[TelemetryDiagnostic, ...]:
        return tuple(self._diagnostics)

    def get_tracer(self, name: str = "pi_agent") -> Tracer:
        if self._closed:
            raise RuntimeError("TracerProvider is closed")
        return Tracer(self, name)

    def emit(self, span: SpanData) -> None:
        if self._closed or not self.processors:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._diagnostics.append(
                TelemetryDiagnostic(
                    "emit",
                    f"span {span.name!r} was dropped because no event loop is running",
                    type(self).__name__,
                )
            )
            return
        task = loop.create_task(self._dispatch(span))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def force_flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for processor in self.processors:
            try:
                await processor.force_flush()
            except Exception as exc:
                self._diagnostics.append(
                    TelemetryDiagnostic("flush", str(exc), type(processor).__name__)
                )

    async def shutdown(self) -> None:
        if self._closed:
            return
        await self.force_flush()
        for processor in reversed(self.processors):
            try:
                await processor.shutdown()
            except Exception as exc:
                self._diagnostics.append(
                    TelemetryDiagnostic("shutdown", str(exc), type(processor).__name__)
                )
        self._closed = True

    async def _dispatch(self, span: SpanData) -> None:
        for processor in self.processors:
            try:
                await processor.on_end(span)
            except Exception as exc:
                self._diagnostics.append(
                    TelemetryDiagnostic("export", str(exc), type(processor).__name__)
                )


__all__ = [
    "BatchSpanProcessor",
    "SimpleSpanProcessor",
    "Span",
    "Tracer",
    "TracerProvider",
]

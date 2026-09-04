from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .redaction import DEFAULT_REDACTOR, TelemetryRedactor
from .types import MetricKind, MetricPoint, TelemetryDiagnostic, TelemetryExporter


@dataclass(slots=True)
class Counter:
    provider: MeterProvider
    name: str

    def add(self, value: int | float = 1, attributes: Mapping[str, Any] | None = None) -> None:
        self.provider.record(self.name, "counter", float(value), attributes)


@dataclass(slots=True)
class Histogram:
    provider: MeterProvider
    name: str

    def record(self, value: int | float, attributes: Mapping[str, Any] | None = None) -> None:
        self.provider.record(self.name, "histogram", float(value), attributes)


@dataclass(slots=True)
class Meter:
    provider: MeterProvider
    name: str

    def counter(self, name: str) -> Counter:
        return Counter(self.provider, f"{self.name}.{name}")

    def histogram(self, name: str) -> Histogram:
        return Histogram(self.provider, f"{self.name}.{name}")


@dataclass(slots=True)
class MeterProvider:
    exporters: Sequence[TelemetryExporter] = ()
    redactor: TelemetryRedactor = field(default_factory=lambda: DEFAULT_REDACTOR)
    _pending: list[MetricPoint] = field(default_factory=list, init=False, repr=False)
    _diagnostics: list[TelemetryDiagnostic] = field(default_factory=list, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.exporters = tuple(self.exporters)

    @property
    def diagnostics(self) -> tuple[TelemetryDiagnostic, ...]:
        return tuple(self._diagnostics)

    def get_meter(self, name: str = "pi_agent") -> Meter:
        if self._closed:
            raise RuntimeError("MeterProvider is closed")
        return Meter(self, name)

    def record(
        self,
        name: str,
        kind: str,
        value: float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if self._closed:
            return
        metric_kind: MetricKind = "counter" if kind == "counter" else "histogram"
        self._pending.append(
            MetricPoint(
                name,
                metric_kind,
                value,
                time.time_ns(),
                self.redactor.attributes(attributes or {}),
            )
        )

    async def force_flush(self) -> None:
        async with self._lock:
            points = tuple(self._pending)
            self._pending.clear()
        if not points:
            return
        for exporter in self.exporters:
            try:
                await exporter.export_metrics(points)
            except Exception as exc:
                self._diagnostics.append(
                    TelemetryDiagnostic("metric_export", str(exc), type(exporter).__name__)
                )

    async def shutdown(self) -> None:
        if self._closed:
            return
        await self.force_flush()
        for exporter in reversed(tuple(self.exporters)):
            try:
                await exporter.shutdown()
            except Exception as exc:
                self._diagnostics.append(
                    TelemetryDiagnostic("metric_shutdown", str(exc), type(exporter).__name__)
                )
        self._closed = True


__all__ = ["Counter", "Histogram", "Meter", "MeterProvider"]

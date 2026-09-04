from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .redaction import TelemetryRedactor
from .types import MetricPoint, SpanData, TelemetryExporter


@dataclass(slots=True)
class InMemoryExporter(TelemetryExporter):
    spans: list[SpanData] = field(default_factory=list)
    metrics: list[MetricPoint] = field(default_factory=list)
    shutdown_called: bool = False

    async def export_spans(self, spans: tuple[SpanData, ...]) -> None:
        self.spans.extend(spans)

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None:
        self.metrics.extend(metrics)

    async def shutdown(self) -> None:
        self.shutdown_called = True


class JsonlExporter(TelemetryExporter):
    """Append telemetry as one JSON object per line under a process lock."""

    def __init__(
        self,
        path: str | Path,
        *,
        fsync: bool = False,
        redacted_values: tuple[str, ...] = (),
        redactor: TelemetryRedactor | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.fsync = fsync
        self.redactor = (redactor or TelemetryRedactor()).with_secrets(redacted_values)
        self._lock = asyncio.Lock()
        self._closed = False

    async def export_spans(self, spans: tuple[SpanData, ...]) -> None:
        await self._append(tuple({"type": "span", **_span_dict(span)} for span in spans))

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None:
        await self._append(tuple({"type": "metric", **asdict(metric)} for metric in metrics))

    async def shutdown(self) -> None:
        self._closed = True

    async def _append(self, records: tuple[dict[str, Any], ...]) -> None:
        if self._closed:
            raise RuntimeError("telemetry exporter is closed")
        if not records:
            return
        encoded = "".join(
            json.dumps(
                self.redactor.redact(record),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for record in records
        )
        async with self._lock:
            await asyncio.to_thread(self._append_sync, encoded)

    def _append_sync(self, encoded: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())
        if os.name != "nt":
            self.path.chmod(0o600)


class CompositeExporter(TelemetryExporter):
    def __init__(self, *exporters: TelemetryExporter) -> None:
        self.exporters = exporters

    async def export_spans(self, spans: tuple[SpanData, ...]) -> None:
        await asyncio.gather(*(exporter.export_spans(spans) for exporter in self.exporters))

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None:
        await asyncio.gather(*(exporter.export_metrics(metrics) for exporter in self.exporters))

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(exporter.shutdown() for exporter in self.exporters),
            return_exceptions=False,
        )


class NullExporter(TelemetryExporter):
    async def export_spans(self, spans: tuple[SpanData, ...]) -> None:
        del spans

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None:
        del metrics

    async def shutdown(self) -> None:
        return


class HttpJsonExporter(TelemetryExporter):
    """Export the project JSON schema to an HTTP endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        redactor: TelemetryRedactor | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("telemetry endpoint must use http or https")
        if timeout <= 0:
            raise ValueError("telemetry timeout must be positive")
        self.endpoint = endpoint
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.redactor = redactor or TelemetryRedactor()

    async def export_spans(self, spans: tuple[SpanData, ...]) -> None:
        await self._export("spans", [_span_dict(span) for span in spans])

    async def export_metrics(self, metrics: tuple[MetricPoint, ...]) -> None:
        await self._export("metrics", [asdict(metric) for metric in metrics])

    async def shutdown(self) -> None:
        return

    async def _export(self, kind: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        payload = json.dumps(
            self.redactor.redact({kind: records}),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await asyncio.to_thread(self._post, payload)

    def _post(self, payload: bytes) -> None:
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"content-type": "application/json", **self.headers},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read(1)


MemoryExporter = InMemoryExporter
MemoryTelemetryExporter = InMemoryExporter
JsonlTelemetryExporter = JsonlExporter
NoopExporter = NullExporter
NoopTelemetryExporter = NullExporter
HttpJsonTelemetryExporter = HttpJsonExporter


def _span_dict(span: SpanData) -> dict[str, Any]:
    value = asdict(span)
    value["durationSeconds"] = span.duration_seconds
    return value


__all__ = [
    "CompositeExporter",
    "HttpJsonExporter",
    "HttpJsonTelemetryExporter",
    "InMemoryExporter",
    "JsonlExporter",
    "JsonlTelemetryExporter",
    "MemoryExporter",
    "MemoryTelemetryExporter",
    "NoopExporter",
    "NoopTelemetryExporter",
    "NullExporter",
]

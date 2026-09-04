from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.telemetry import (
    JsonlTelemetryExporter,
    MemoryTelemetryExporter,
    SimpleSpanProcessor,
    TelemetryRedactor,
    TracerProvider,
)


@pytest.mark.asyncio
async def test_nested_spans_keep_parentage_and_redact_secrets() -> None:
    exporter = MemoryTelemetryExporter()
    provider = TracerProvider(
        (SimpleSpanProcessor(exporter),),
        redactor=TelemetryRedactor(secrets=("secret-value",)),
    )
    tracer = provider.get_tracer("test")

    with (
        tracer.span("root", attributes={"authorization": "secret-value"}) as root,
        tracer.span("child", attributes={"value": "has secret-value"}) as child,
    ):
        child.add_event("step", {"token": "secret-value"})
    await provider.force_flush()

    assert root.context.trace_id == child.context.trace_id
    assert child.context.parent_span_id == root.context.span_id
    spans = {span.name: span for span in exporter.spans}
    child_data = spans["child"]
    root_data = spans["root"]
    assert child_data.attributes["value"] == "has [REDACTED]"
    assert child_data.events[0].attributes["token"] == "[REDACTED]"
    assert root_data.attributes["authorization"] == "[REDACTED]"
    await provider.shutdown()


@pytest.mark.asyncio
async def test_exporter_failure_is_diagnostic_not_span_failure() -> None:
    class BrokenExporter:
        async def export_spans(self, _spans):
            raise RuntimeError("collector offline")

        async def export_metrics(self, _points):
            raise RuntimeError("collector offline")

        async def shutdown(self):
            raise RuntimeError("shutdown offline")

    provider = TracerProvider((SimpleSpanProcessor(BrokenExporter()),))
    tracer = provider.get_tracer()
    with tracer.span("operation"):
        pass
    await provider.shutdown()
    assert [item.operation for item in provider.diagnostics] == ["export", "shutdown"]


@pytest.mark.asyncio
async def test_jsonl_exporter_writes_complete_records(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    exporter = JsonlTelemetryExporter(path)
    provider = TracerProvider((SimpleSpanProcessor(exporter),))
    with provider.get_tracer().span("write"):
        pass
    await provider.shutdown()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["type"] == "span"
    assert records[0]["name"] == "write"
    assert records[0]["status"] == "ok"

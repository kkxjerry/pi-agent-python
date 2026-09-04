from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent, Usage
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.telemetry import (
    InMemoryExporter,
    JsonlExporter,
    Meter,
    Tracer,
    attach_agent_telemetry,
)


def model() -> Model:
    return Model(
        api="openai-completions",
        provider="fixture",
        id="fixture-model",
        name="fixture-model",
        context_window=32_000,
        max_tokens=4_000,
    )


@pytest.mark.asyncio
async def test_tracer_preserves_parentage_status_and_metrics() -> None:
    exporter = InMemoryExporter()
    tracer = Tracer(exporter, service_name="test")
    meter = Meter(exporter)

    async with tracer.span("parent") as parent:
        async with tracer.span("child", kind="client") as child:
            child.add_event("request", {"attempt": 1})
        await meter.record("requests", 1)

    assert [span.name for span in exporter.spans] == ["child", "parent"]
    assert exporter.spans[0].parent_span_id == parent.span_id
    assert exporter.spans[0].trace_id == exporter.spans[1].trace_id
    assert exporter.spans[0].status == "ok"
    assert exporter.metrics[0].name == "requests"


@pytest.mark.asyncio
async def test_jsonl_exporter_redacts_secrets(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    exporter = JsonlExporter(path, redacted_values=("secret-value",))
    tracer = Tracer(exporter)
    span = tracer.start_span("call", attributes={"authorization": "secret-value"})
    await span.end()
    await tracer.shutdown()

    record = json.loads(path.read_text(encoding="utf-8"))
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert record["attributes"]["authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_agent_telemetry_records_run_usage(tmp_path: Path) -> None:
    settings = SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(tmp_path / "missing"),
            "compaction.enabled": False,
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
            "extensions.enabled": False,
            "models.snapshot_file": str(tmp_path / "models.json"),
            "auth.file": str(tmp_path / "auth.json"),
        },
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[TextContent("done")],
                stop_reason="stop",
                usage=Usage(input=4, output=2, total_tokens=6),
            )
        ]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model(),
                stream_fn=provider.stream,
                settings=settings,
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    exporter = InMemoryExporter()
    telemetry = attach_agent_telemetry(session, Tracer(exporter))

    await session.prompt("hello")
    await telemetry.close()

    assert any(span.name == "agent.run" and span.status == "ok" for span in exporter.spans)
    metrics = {point.name: point.value for point in exporter.metrics}
    assert metrics["agent.runs"] == 1
    assert metrics["tokens.total"] == 6
    await session.close()

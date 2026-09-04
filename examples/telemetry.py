from __future__ import annotations

import asyncio
from pathlib import Path

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent
from pi_agent.coding_agent import CreateAgentSessionOptions, create_agent_session
from pi_agent.telemetry import (
    JsonlTelemetryExporter,
    MeterProvider,
    SimpleSpanProcessor,
    TracerProvider,
    instrument_agent_session,
)


async def main() -> None:
    model = Model(
        api="fixture",
        provider="fixture",
        id="fixture",
        name="Fixture",
        context_window=8192,
        max_tokens=1024,
    )
    provider = FauxProvider([AssistantMessage(content=[TextContent("done")], stop_reason="stop")])
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=Path.cwd(),
                model=model,
                stream_fn=provider.stream,
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    exporter = JsonlTelemetryExporter(".pi/telemetry.jsonl")
    traces = TracerProvider((SimpleSpanProcessor(exporter),))
    metrics = MeterProvider((exporter,))
    instrumentation = instrument_agent_session(session, traces, metrics)
    try:
        await session.prompt("Demonstrate telemetry")
    finally:
        await instrumentation.close()
        await traces.shutdown()
        await metrics.shutdown()
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())

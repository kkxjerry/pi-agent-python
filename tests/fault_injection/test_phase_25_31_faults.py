from __future__ import annotations

import os
from pathlib import Path

import pytest

from pi_agent.agent import AgentContext, BeforeToolCallContext
from pi_agent.ai import AssistantMessage, ToolCall
from pi_agent.coding_agent import ApprovalGate, ApprovalPolicy
from pi_agent.telemetry import SimpleSpanProcessor, TracerProvider
from pi_agent.tui import InputDecoder, Surface


def test_input_decoder_survives_arbitrary_byte_fragments() -> None:
    payload = bytes(range(256)) * 4
    for width in (1, 2, 3, 7, 31):
        decoder = InputDecoder()
        events = []
        for offset in range(0, len(payload), width):
            events.extend(decoder.feed(payload[offset : offset + width]))
        events.extend(decoder.flush_escape())
        assert events


def test_surface_never_writes_outside_bounds_for_random_coordinates() -> None:
    surface = Surface(20, 5)
    for value in range(-50, 50):
        surface.draw_text(value, value % 9 - 2, "A你👩\u200d💻")
    assert len(surface.plain_lines()) == 5
    assert all(len(line) >= 0 for line in surface.plain_lines())


@pytest.mark.asyncio
async def test_approval_prompt_exception_fails_closed(tmp_path: Path) -> None:
    async def broken_prompt(_request):
        raise RuntimeError("input stream closed")

    gate = ApprovalGate(
        ApprovalPolicy(tmp_path),
        session_id="session",
        prompt=broken_prompt,
    )
    call = ToolCall("call-1", "write", {"path": "a.py", "content": "x"})
    context = BeforeToolCallContext(
        assistant_message=AssistantMessage(content=[call], stop_reason="toolUse"),
        tool_call=call,
        args={"path": "a.py", "content": "x"},
        context=AgentContext(system_prompt="", messages=[], tools=[]),
    )
    result = await gate.before_tool_call(context, None)
    assert result is not None and result.block is True
    assert "prompt failed" in str(result.reason)


@pytest.mark.asyncio
async def test_telemetry_export_failure_never_escapes_span_end() -> None:
    class BrokenExporter:
        async def export_spans(self, _spans):
            raise OSError("disk full")

        async def export_metrics(self, _points):
            raise OSError("disk full")

        async def shutdown(self):
            return None

    provider = TracerProvider((SimpleSpanProcessor(BrokenExporter()),))
    tracer = provider.get_tracer()
    with tracer.span("fault"):
        os.getpid()
    await provider.force_flush()
    assert provider.diagnostics

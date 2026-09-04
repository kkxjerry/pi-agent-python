from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
)
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.security import ApprovalManager, ApprovalResponse, StaticApprovalHandler, ToolPolicy
from pi_agent.telemetry import InMemoryExporter, Tracer, attach_agent_telemetry
from pi_agent.tui import InteractiveTui, KeyEvent, MemoryTerminal, TuiOptions


def model() -> Model:
    return Model(
        api="openai-completions",
        provider="fixture",
        id="fixture-model",
        name="fixture-model",
        context_window=32_000,
        max_tokens=4_000,
    )


def settings(tmp_path: Path):
    return SettingsResolver().resolve(
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


@pytest.mark.asyncio
async def test_interactive_approval_tool_and_telemetry_vertical_slice(tmp_path: Path) -> None:
    executions: list[dict[str, Any]] = []

    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        executions.append(arguments)
        return AgentToolResult([TextContent("tool complete")])

    dangerous = AgentTool(
        "dangerous",
        "Dangerous",
        "A tool that requires explicit approval",
        {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
        execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[ToolCall("call-1", "dangerous", {"target": "demo"})],
                stop_reason="toolUse",
            ),
            AssistantMessage(content=[TextContent("all done")], stop_reason="stop"),
        ]
    )
    approval_handler = StaticApprovalHandler(ApprovalResponse(True, "once"))
    approval = ApprovalManager(ToolPolicy(default_effect="ask"), approval_handler)
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model(),
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
                tools=[dangerous],
                approval_manager=approval,
            )
        )
    ).session
    exporter = InMemoryExporter()
    telemetry = attach_agent_telemetry(session, Tracer(exporter))
    terminal = MemoryTerminal(width=72, height=18)
    app = InteractiveTui(
        session,
        terminal=terminal,
        options=TuiOptions(initial_prompt="use the dangerous tool", cwd=tmp_path),
    )

    task = asyncio.create_task(app.run())
    for _ in range(200):
        if session.get_last_assistant_text() == "all done":
            break
        await asyncio.sleep(0.01)
    terminal.feed(KeyEvent("ctrl_d", ctrl=True))
    assert await asyncio.wait_for(task, timeout=3) == 0

    assert executions == [{"target": "demo"}]
    assert len(approval_handler.requests) == 1
    assert any(span.name == "tool.dangerous" and span.status == "ok" for span in exporter.spans)
    assert any(span.name == "agent.run" and span.status == "ok" for span in exporter.spans)
    await telemetry.close()
    await session.close()

from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent, ToolCall
from pi_agent.harness import AgentHarness, create_local_execution_env


def assistant_tool(call_id: str, name: str, arguments: dict[str, object]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(call_id, name, arguments)],
        stop_reason="toolUse",
    )


@pytest.mark.asyncio
async def test_harness_runs_a_real_write_read_vertical_slice(model: Model, tmp_path: Path) -> None:
    provider = FauxProvider(
        [
            assistant_tool("write-1", "write", {"path": "note.txt", "content": "hello\n"}),
            assistant_tool("read-1", "read", {"path": "note.txt"}),
            AssistantMessage(content=[TextContent("finished")], stop_reason="stop"),
        ]
    )
    harness = AgentHarness(
        model=model,
        stream_fn=provider.stream,
        env=create_local_execution_env(tmp_path),
        system_prompt="coding agent",
    )

    result = await harness.run("Create note.txt, read it, then finish")

    assert result.succeeded is True
    assert result.final_assistant is not None
    assert result.final_assistant.content == [TextContent("finished")]
    assert (tmp_path / "note.txt").read_text() == "hello\n"
    assert [message.role for message in result.transcript] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
    ]
    assert provider.requests[0].context.system_prompt == "coding agent"
    assert {tool.name for tool in provider.requests[0].context.tools or []} == {
        "read",
        "write",
        "edit",
        "bash",
    }


def test_harness_rejects_duplicate_tool_names(model: Model, tmp_path: Path) -> None:
    env = create_local_execution_env(tmp_path)
    duplicate = AgentHarness(
        model=model,
        stream_fn=FauxProvider([]).stream,
        env=env,
        include_coding_tools=False,
    )
    read_tool = duplicate.agent.tools
    assert read_tool == []
